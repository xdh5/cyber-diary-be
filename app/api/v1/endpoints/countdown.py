from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.auth import get_current_user
from app.crud.crud import (
    create_countdown,
    delete_countdown,
    get_countdown_by_id_and_user,
    get_countdowns_by_user,
    update_countdown,
)
from app.db.session import get_db
from app.models.models import Countdown
from app.schemas.schemas import CountdownCreate, CountdownResponse, CountdownUpdate

router = APIRouter()


@router.get("/", response_model=List[CountdownResponse])
def list_countdowns(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get all countdowns for the current user, sorted by target date."""
    return get_countdowns_by_user(db, current_user.id)


@router.post("/", response_model=CountdownResponse, status_code=status.HTTP_201_CREATED)
def create_countdown_entry(
    countdown: CountdownCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new countdown."""
    new_countdown = Countdown(
        user_id=current_user.id,
        name=countdown.name,
        target_date=countdown.target_date,
    )
    return create_countdown(db, new_countdown)


@router.get("/{countdown_id}", response_model=CountdownResponse)
def get_countdown(
    countdown_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get a specific countdown by ID."""
    countdown = get_countdown_by_id_and_user(db, countdown_id, current_user.id)
    if not countdown:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Countdown not found",
        )
    return countdown


@router.patch("/{countdown_id}", response_model=CountdownResponse)
def update_countdown_entry(
    countdown_id: int,
    countdown_data: CountdownUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update a countdown."""
    countdown = get_countdown_by_id_and_user(db, countdown_id, current_user.id)
    if not countdown:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Countdown not found",
        )

    if countdown_data.name is not None:
        countdown.name = countdown_data.name
    if countdown_data.target_date is not None:
        countdown.target_date = countdown_data.target_date

    return update_countdown(db, countdown)


@router.delete("/{countdown_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_countdown_entry(
    countdown_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete a countdown."""
    countdown = get_countdown_by_id_and_user(db, countdown_id, current_user.id)
    if not countdown:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Countdown not found",
        )
    delete_countdown(db, countdown)
