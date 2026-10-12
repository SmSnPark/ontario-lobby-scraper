from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


LobbyistType = Literal[
    "Consultant Lobbyist",
    "In-house Organization",
    "In-house Persons & Partnerships",
]


class RegistrationStatus(BaseModel):
    initial_filing_date: date
    last_filed_date: date


class Organization(BaseModel):
    name: str
    address: str
    #legit empty for In-house Persons & Partnerships records 
    description: str = ""


class Lobbyist(BaseModel):
    name: str
    position: str | None = None


class LobbyingActivity(BaseModel):
    category: str
    goal: str
    targets: list[str] = Field(default_factory=list)


class GovernmentFunding(BaseModel):
    source: str
    amount: float

    @field_validator("amount", mode="before")
    @classmethod
    def _strip_currency(cls, v):
        if isinstance(v, str):
            return v.replace("$", "").replace(",", "").strip()
        return v


class ParsedRegistration(BaseModel):
    registration_number: str = Field(min_length=1)
    lobbyist_type: LobbyistType
    status: RegistrationStatus
    organization: Organization
    lobbyists: list[Lobbyist]
    subject_matters: list[str] = Field(default_factory=list)
    lobbying_activities: list[LobbyingActivity] = Field(default_factory=list)
    government_funding: list[GovernmentFunding] = Field(default_factory=list)
