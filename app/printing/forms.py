from flask_wtf import FlaskForm
from wtforms import HiddenField, IntegerField, SelectField
from wtforms.validators import InputRequired, NumberRange

from ..models import PAPER_SIZES


class PrintOptionsForm(FlaskForm):
    copies = IntegerField("Copies", validators=[InputRequired(), NumberRange(min=1)])
    paper_size = SelectField("Paper size", choices=[(p, p) for p in PAPER_SIZES])
    color = SelectField("Colour", choices=[("", "Printer default"), ("color", "Colour"), ("mono", "Black and white")])
    orientation = SelectField("Orientation", choices=[("", "Document default"), ("portrait", "Portrait"), ("landscape", "Landscape")])
    request_id = HiddenField()  # one per page view: a double click or back-and-resubmit cannot print twice
