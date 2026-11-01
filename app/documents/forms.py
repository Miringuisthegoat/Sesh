from flask_wtf import FlaskForm


class CsrfOnlyForm(FlaskForm):
    """Used for upload (file is read manually so we control size/validation) and for POST actions."""
