from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('api', '0007_bien_valorisation_heure_proprietaire')]

    operations = [
        migrations.AddField(
            model_name='frais',
            name='facture_path',
            field=models.CharField(max_length=1024, null=True, blank=True),
        ),
        migrations.RemoveField(
            model_name='frais',
            name='facture',
        ),
    ]
