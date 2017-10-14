# -*- coding: utf-8 -*-
# Generated by Django 1.10.8 on 2017-10-16 16:03
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mdm', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='EnrolledDevice',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('serial_number', models.TextField(db_index=True)),
                ('udid', models.CharField(max_length=36, unique=True)),
                ('token', models.BinaryField(blank=True, null=True)),
                ('push_magic', models.TextField(blank=True, null=True)),
                ('unlock_token', models.BinaryField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('push_certificate', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='mdm.PushCertificate')),
            ],
        ),
    ]
