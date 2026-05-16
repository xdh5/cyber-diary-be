from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.timezone import now_shanghai
from app.crud.crud import (
    create_todo_group,
    delete_todo_group,
    get_default_todo_group,
    get_todo_group_by_id_and_user,
    get_todo_groups_by_user,
    update_todo_group,
)
from app.db.session import get_db
from app.models.models import Todo, TodoGroup
from app.schemas.schemas import TodoGroupCreate, TodoGroupResponse, TodoGroupUpdate

router = APIRouter()


def _to_group_response(group: TodoGroup) -> dict:
    return {
        'id': group.id,
        'name': group.name,
        'is_default': group.is_default,
        'created_at': group.created_at,
        'updated_at': group.updated_at,
    }


@router.get("/", response_model=List[TodoGroupResponse])
def list_todo_groups(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get all todo groups for the current user."""
    groups = get_todo_groups_by_user(db, current_user.id)
    return [_to_group_response(g) for g in groups]


@router.post("/", response_model=TodoGroupResponse, status_code=status.HTTP_201_CREATED)
def create_todo_group_entry(
    group: TodoGroupCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new todo group."""
    new_group = TodoGroup(
        user_id=current_user.id,
        name=group.name,
        is_default=group.is_default or False,
    )
    created = create_todo_group(db, new_group)
    return _to_group_response(created)


@router.get("/{group_id}", response_model=TodoGroupResponse)
def get_todo_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get a specific todo group by ID."""
    group = get_todo_group_by_id_and_user(db, group_id, current_user.id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todo group not found",
        )
    return _to_group_response(group)


@router.patch("/{group_id}", response_model=TodoGroupResponse)
def update_todo_group_entry(
    group_id: int,
    group_data: TodoGroupUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update a todo group."""
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
    return _to_group_response(updated)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_todo_group_entry(
    group_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete a todo group."""
    group = get_todo_group_by_id_and_user(db, group_id, current_user.id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todo group not found",
        )

    # Check if group is default
    if group.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete default group",
        )

    # Reassign todos to default group
    default_group = get_default_todo_group(db, current_user.id)
    todos = db.exec(
        Todo.__table__.update()
        .where(Todo.group_id == group_id)
        .values(group_id=default_group.id if default_group else None)
    )
    db.commit()

    delete_todo_group(db, group)