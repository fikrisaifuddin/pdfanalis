from django import template

register = template.Library()


@register.filter
def rupiah(value, decimals=0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0

    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        decimals = 0

    formatted = "{:,.{dec}f}".format(value, dec=decimals)

    formatted = formatted.replace(",", "TEMP_SEP").replace(".", ",").replace("TEMP_SEP", ".")

    return formatted