# Generated by Django 4.2.7 on 2024-05-28 00:43

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pt_app', '0039_remove_user_public_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='guest',
            field=models.BooleanField(default=False),
        ),
    ]
