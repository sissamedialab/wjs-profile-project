import re

from django import template

register = template.Library()


@register.tag(name="angular_variables")
def do_angular_variables(parser, token):
    nodelist = parser.parse(("endangular_variables",))
    parser.delete_first_token()
    return CustomParseNode(nodelist)


class CustomParseNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        output = self.nodelist.render(context)
        return re.sub(r"<\s*(\w+)\s*>", lambda match: str(context.get(match.group(1), "")), output)
