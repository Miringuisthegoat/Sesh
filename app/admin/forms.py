from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from ..models import PAPER_SIZES


class InviteForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=150)])
    email = StringField("Email", validators=[DataRequired(), Length(max=254)])

    
class PasswordResetForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), Length(max=128)])


class CompanySettingsForm(FlaskForm):
    name = StringField("Company name", validators=[DataRequired(), Length(max=200)])
    default_paper_size = SelectField("Default paper size", choices=[(p, p) for p in PAPER_SIZES])
    max_copies = IntegerField("Maximum copies per print job", validators=[DataRequired(), NumberRange(min=1)])
    retention_days = IntegerField("Keep documents for (days)", validators=[DataRequired(), NumberRange(min=1, max=3650)])
    workers_can_view_all_documents = BooleanField("Workers may view other workers' documents")


class OfficeForm(FlaskForm):
    name = StringField("Office name", validators=[DataRequired(), Length(max=150)])
    location = StringField("Location / address (optional)", validators=[Optional(), Length(max=255)])
