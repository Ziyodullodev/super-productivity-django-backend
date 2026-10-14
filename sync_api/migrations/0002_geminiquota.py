from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sync_api', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='GeminiQuota',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider_id', models.CharField(db_index=True, max_length=160, unique=True)),
                ('cooldown_until', models.FloatField(default=0.0)),
                ('last_error', models.CharField(blank=True, max_length=200)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
