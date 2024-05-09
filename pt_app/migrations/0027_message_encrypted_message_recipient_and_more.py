# Generated by Django 4.2.7 on 2024-05-08 18:59

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pt_app', '0026_user_public_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='encrypted_message_recipient',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='message',
            name='encrypted_message_sender',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='message',
            name='nonce_recipient',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='message',
            name='nonce_sender',
            field=models.TextField(blank=True, null=True),
        ),
    ]
