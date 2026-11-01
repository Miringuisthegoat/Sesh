from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField
from wtforms.validators import DataRequired, EqualTo, Length


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])


class SetupForm(FlaskForm):
    company_name = StringField("Company name", validators=[DataRequired(), Length(max=200)])
    full_name = StringField("Your full name", validators=[DataRequired(), Length(max=150)])
    email = StringField("Your email", validators=[DataRequired(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    confirm = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", "Passwords do not match.")])
