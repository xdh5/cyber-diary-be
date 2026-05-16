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
)
from app.db.session import get_db
from app.models.models import Todo
from app.schemas.schemas import TodoCreate, TodoResponse, TodoUpdate

router = APIRouter()


def _to_todo_response(todo: Todo) -> dict:
    return {
        'id': todo.id,
        'title': todo.title,
        'description': todo.description,
        'status': todo.status,
        'deadline': todo.deadline,
        'completed_at': todo.completed_at,
        'created_at': todo.created_at,
        'updated_at': todo.updated_at,
    }


@router.get("/", response_model=List[TodoResponse])
def list_todos(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get all todos for the current user."""
    todos = get_todos_by_user(db, current_user.id)
    return [_to_todo_response(t) for t in todos]


@router.post("/", response_model=TodoResponse, status_code=status.HTTP_201_CREATED)
def create_todo_entry(
    todo: TodoCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new todo."""
    new_todo = Todo(
        user_id=current_user.id,
        title=todo.title,
        description=todo.description,
        deadline=todo.deadline,
    )
    created = create_todo(db, new_todo)
    return _to_todo_response(created)


@router.get("/{todo_id}", response_model=TodoResponse)
def get_todo(
    todo_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
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
    current_user=Depends(get_current_user),
):
    """Update a todo."""
    todo = get_todo_by_id_and_user(db, todo_id, current_user.id)
    if not todo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todo not found",
        )

    update_data = todo_data.dict(exclude_unset=True)
    
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
    current_user=Depends(get_current_user),
):
    """Delete a todo."""
    todo = get_todo_by_id_and_user(db, todo_id, current_user.id)
    if not todo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todo not found",
        )
    delete_todo(db, todo)