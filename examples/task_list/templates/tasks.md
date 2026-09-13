# My task list
{% for group in groups %}

## {{ group.name }}

{% for task in group.tasks %}
- {{ task }}
{% endfor %}
{% endfor %}
