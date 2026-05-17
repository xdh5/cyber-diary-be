from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.timezone import now_shanghai
from app.crud.crud import (
    create_todo,
    delete_todo,
    get_todo_by_id_and_user,
    get_todos_by_user,
    update_todo,
    get_todo_groups_by_user,
    get_todo_group_by_id_and_user,
    create_todo_group,
    update_todo_group,
    delete_todo_group,
)
from app.db.session import get_db
from app.models.models import Todo, TodoGroup
from app.schemas.schemas import TodoCreate, TodoResponse, TodoUpdate, TodoGroupCreate, TodoGroupResponse, TodoGroupUpdate

router = APIRouter()


def _to_todo_response(todo: Todo) -> dict:
    return {
        'id': todo.id,
        'title': todo.title,
        'description': todo.description,
        'status': todo.status,
        'deadline': todo.deadline,
        'group_id': todo.group_id,
        'completed_at': todo.completed_at,
        'created_at': todo.created_at,
        'updated_at': todo.updated_at,
    }


def _to_todo_group_response(group: TodoGroup) -> dict:
    return {
        'id': group.id,
        'name': group.name,
        'created_at': group.created_at,
        'updated_at': group.updated_at,
    }


# Todo Group endpoints
@router.get("/groups/", response_model=List[TodoGroupResponse])
def list_todo_groups(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Get all todo groups for the current user, including the default "日常" group."""
    groups = get_todo_groups_by_user(db, current_user.id)
    
    # Add default group at the beginning (not stored in DB)
    default_group = {
        'id': 0,
        'name': '日常',
        'created_at': now_shanghai(),
        'updated_at': now_shanghai(),
    }
    
    return [default_group] + [_to_todo_group_response(g) for g in groups]


@router.post("/groups/", response_model=TodoGroupResponse, status_code=status.HTTP_201_CREATED)
def create_todo_group_entry(
    group: TodoGroupCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Create a new todo group."""
    new_group = TodoGroup(
        user_id=current_user.id,
        name=group.name,
    )
    created = create_todo_group(db, new_group)
    return _to_todo_group_response(created)


@router.patch("/groups/{group_id}", response_model=TodoGroupResponse)
def update_todo_group_entry(
    group_id: int,
    group_data: TodoGroupUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Update a todo group."""
    # Cannot update default group
    if group_id == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update default group",
        )
    
    group = get_todo_group_by_id_and_user(db, group_id, current_user.id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todo group not found",
        )

    update_data = group_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(group, field, value)

    updated = update_todo_group(db, group)
    return _to_todo_group_response(updated)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_todo_group_entry(
    group_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Delete a todo group."""
    # Cannot delete default group
    if group_id == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete default group",
        )
    
    group = get_todo_group_by_id_and_user(db, group_id, current_user.id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todo group not found",
        )
    
    # Unset group_id from todos in this group
    todos = get_todos_by_user(db, current_user.id)
    for todo in todos:
        if todo.group_id == group_id:
            todo.group_id = None
            update_todo(db, todo)
    
    delete_todo_group(db, group)


# Todo endpoints
@router.get("/", response_model=List[TodoResponse])
def list_todos(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Get all todos for the current user."""
    todos = get_todos_by_user(db, current_user.id)
    return [_to_todo_response(t) for t in todos]


@router.post("/", response_model=TodoResponse, status_code=status.HTTP_201_CREATED)
def create_todo_entry(
    todo: TodoCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Create a new todo."""
    new_todo = Todo(
        user_id=current_user.id,
        title=todo.title,
        description=todo.description,
        deadline=todo.deadline,
        group_id=todo.group_id if todo.group_id != 0 else None,  # default group uses None
    )
    created = create_todo(db, new_todo)
    return _to_todo_response(created)


@router.get("/{todo_id}", response_model=TodoResponse)
def get_todo(
    todo_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Get a specific todo by ID."""
    todo = get_todo_by_id_and_user(db, todo_id, current_user.id)
    if not todo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todo not found",
        )
    return _to_todo_response(todo)


@router.patch("/{todo_id}", response_model=TodoResponse)
def update_todo_entry(
    todo_id: int,
    todo_data: TodoUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Update a todo."""
    todo = get_todo_by_id_and_user(db, todo_id, current_user.id)
    if not todo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todo not found",
        )

    update_data = todo_data.dict(exclude_unset=True)
    
    # Handle group_id: 0 means default group (None in DB)
    if 'group_id' in update_data:
        if update_data['group_id'] == 0:
            update_data['group_id'] = None
    
    # Handle status transitions
    if 'status' in update_data:
        new_status = update_data['status']
        if new_status == 'completed' and todo.status != 'completed':
            todo.completed_at = now_shanghai()
        elif new_status != 'completed':
            todo.completed_at = None
    
    for field, value in update_data.items():
        setattr(todo, field, value)

    updated = update_todo(db, todo)
    return _to_todo_response(updated)


@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_todo_entry(
    todo_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Delete a todo."""
    todo = get_todo_by_id_and_user(db, todo_id, current_user.id)
    if not todo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todo not found",
        )
    delete_todo(db, todo)
