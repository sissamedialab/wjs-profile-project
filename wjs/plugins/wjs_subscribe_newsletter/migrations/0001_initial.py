# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2023-02-28 15:52
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('journal', '__first__'),
    ]

    operations = [
        migrations.CreateModel(
            name='PluginConfig',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(help_text='Section title', max_length=500)),
                ('intro', models.CharField(help_text='Introduction text', max_length=500)),
                ('journal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='wjs_subscribe_newsletter_plugin_config', to='journal.Journal')),
            ],
        ),
    ]
