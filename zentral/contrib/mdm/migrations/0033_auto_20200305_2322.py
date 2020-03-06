# Generated by Django 2.2.10 on 2020-03-05 23:22

from django.db import migrations
import plistlib


def create_body_from_dictionary(apps, schema_editor):
    DeviceCommand = apps.get_model("mdm", "DeviceCommand")
    for dc in DeviceCommand.objects.all():
        dc.dictionary["RequestType"] = dc.request_type
        body = {"Command": dc.dictionary,
                "CommandUUID": str(dc.uuid)}
        dc.body = plistlib.dumps(body).decode("utf-8")
        dc.save()


class Migration(migrations.Migration):

    dependencies = [
        ('mdm', '0032_devicecommand_body'),
    ]

    operations = [
        migrations.RunPython(create_body_from_dictionary)
    ]