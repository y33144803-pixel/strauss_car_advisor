"""אובייקט המצב (State) של שיחה - מנגנון הזיכרון של המערכת.

הנתונים שהעובד מסר נשמרים כאן (ולא בזיכרון של מודל השפה), וכלי הסוכן קוראים מכאן.
המצב נשמר ב-SQLite אחרי כל פעולה, כך שרענון העמוד לא מאפס את התהליך.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

# שדות עובד שחובה למלא לפני חישוב (0 הוא ערך חוקי לעמלות/בונוס/קרן השתלמות)
REQUIRED_EMPLOYEE_FIELDS = {
    "eligibility_group": "קבוצת זכאות",
    "base_salary": "שכר בסיס",
    "commissions": "עמלות",
    "bonus": "בונוס",
    "pension_rate": "אחוז הפרשה לפנסיה",
    "study_fund_rate": "אחוז הפרשה לקרן השתלמות",
    "credit_points": "נקודות זיכוי",
}


class EmployeeData(BaseModel):
    eligibility_group: Optional[str] = None
    base_salary: Optional[float] = None
    commissions: Optional[float] = None
    bonus: Optional[float] = None
    pension_rate: Optional[float] = None
    study_fund_rate: Optional[float] = None
    credit_points: Optional[float] = None
    occupation: Optional[str] = None          # נדרש רק לוויתור על רכב
    group_source: Optional[Literal["employee", "occupation"]] = None   # מי קבע את קבוצת הזכאות
    group_conflict: Optional[str] = None      # סתירה פתוחה בין הקבוצה שהעובד ציין לקבוצת העיסוק בטבלה
    children_under_23: Optional[int] = None   # נדרש רק למסלול ברוכת ילדים
    children_ages: Optional[list[int]] = None  # גילאי הילדים, אם העובד מסר (העוגן לספירה ולהסבר דחייה)


class CurrentSituation(BaseModel):
    """המצב היום: רכב, ויתור (אחזקת רכב) או ללא רכב."""
    kind: Optional[Literal["car", "waiver", "none"]] = None
    label: Optional[str] = None
    catalog_id: Optional[str] = None
    registration_year: Optional[int] = None
    car_type_code: Optional[str] = None
    make_code: Optional[str] = None
    model_code: Optional[str] = None
    usage_value: Optional[float] = None
    is_electric: bool = False
    participation: Optional[float] = None
    maintenance: float = 0


class TargetChoice(BaseModel):
    """החלופה המבוקשת: רכב מהקטלוג או ויתור."""
    kind: Optional[Literal["car", "waiver"]] = None
    label: Optional[str] = None
    catalog_id: Optional[str] = None
    car_group: Optional[str] = None
    usage_value: float = 0
    is_electric: bool = False
    monthly_cost: Optional[float] = None
    budget: Optional[float] = None
    family_employee_share: float = 0
    maintenance: float = 0
    eligibility_reason: Optional[str] = None
    participation: Optional[float] = None
    # confirmed = מהעובד או מהנציג; provisional = ברירת מחדל מהקטלוג עד תשובת הנציג
    participation_status: Literal["missing", "provisional", "confirmed"] = "missing"
    participation_effective_from: Optional[str] = None


class ConversationState(BaseModel):
    session_id: str
    employee: EmployeeData = Field(default_factory=EmployeeData)
    current: CurrentSituation = Field(default_factory=CurrentSituation)
    target: TargetChoice = Field(default_factory=TargetChoice)
    car_candidates: list[dict] = Field(default_factory=list)  # תוצאות חיפוש הרכב הנוכחי לבחירת העובד
    picker_open: bool = False  # בורר הרכבים שמעל תיבת השיחה פתוח (נפתח ע"י הכלי show_catalog או ע"י העובד)
    waiting_for_representative: bool = False
    pending_questions: dict[str, int] = Field(default_factory=dict)  # שדה חסר -> מזהה שאלה פתוחה לנציג
    # שדה חסר -> מזהה רכב הקטלוג שעליו נשאלה השאלה. תשובת הנציג מחושבת על הרכב הזה גם אם בינתיים נבדקה חלופה אחרת
    pending_inquiry_car: dict[str, str] = Field(default_factory=dict)
    last_result: Optional[dict] = None
    result_is_stale: bool = False

    def missing_required_fields(self) -> list[str]:
        """שמות (בעברית) של הנתונים שעדיין חסרים לחישוב."""
        missing = [label for field, label in REQUIRED_EMPLOYEE_FIELDS.items()
                   if getattr(self.employee, field) is None]
        if self.current.kind is None:
            missing.append("המצב היום (רכב נוכחי / אחזקה / ללא רכב)")
        elif self.current.kind == "car" and self.current.participation is None:
            missing.append("השתתפות עובד ברכב הנוכחי")
        if self.target.kind is None:
            missing.append("החלופה המבוקשת (רכב מהקטלוג או ויתור)")
        elif self.target.kind == "car" and self.target.participation is None:
            missing.append("השתתפות עובד ברכב החדש")
        return missing

    @property
    def blocking_pending(self) -> dict[str, int]:
        """שאלות פתוחות לנציג שמעכבות את החישוב הנוכחי. שאלה על השתתפות ברכב אחר (שנשאלה לפני שהעובד בדק חלופה
        אחרת, למשל ויתור) לא מעכבת - היא תיקלט וייחושב עליה כשהנציג יענה."""
        blocking = {}
        for field, question_id in self.pending_questions.items():
            asked_about = self.pending_inquiry_car.get(field)
            if field == "new_participation" and asked_about and asked_about != self.target.catalog_id:
                continue
            blocking[field] = question_id
        return blocking

    @property
    def calculation_ready(self) -> bool:
        return not self.missing_required_fields() and not self.blocking_pending

    def invalidate_result(self) -> None:
        """נתון השתנה - התוצאה הקודמת כבר לא עדכנית ויש לחשב מחדש."""
        if self.last_result is not None:
            self.result_is_stale = True
