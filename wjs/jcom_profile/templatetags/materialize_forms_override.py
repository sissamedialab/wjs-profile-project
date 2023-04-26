from django.forms import CheckboxInput
from django.template.loader_tags import register
from materialize.templatetags.utils_forms import get_field_type, render_checkbox, render_checkbox_input, render_radio, \
    render_file

@register.filter(name='file_input')
def field_type(field):
    name = get_field_type(field)
    if name == 'ClearableFileInput' or name == 'FileInput':
        return True
    return False
@register.filter(name='addcss')
def addcss(field, css):
    type_input = get_field_type(field)
    existing_attrs = field.field.widget.attrs
    if type_input == 'Textarea':
        css = 'materialize-textarea'
    if type_input == 'CheckboxSelectMultiple':
        return render_checkbox(field)
    if isinstance(field.field.widget, CheckboxInput):
        return render_checkbox_input(field)
    if type_input == 'RadioSelect':
        return render_radio(field)
    if type_input == 'ClearableFileInput':
        return render_file(field)
    if type_input == 'DateInput':
        return field.as_widget(attrs={"type": 'date', "class": 'datepicker', **existing_attrs})
    return field.as_widget(attrs={"class": css, **existing_attrs})
