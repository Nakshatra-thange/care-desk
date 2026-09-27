# Written by hand, runs before the tables are created.
# The constraint compares therapist_id with "=" inside a GiST index.
# Plain integers can't go in a GiST index without the btree_gist extension.
from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [BtreeGistExtension()]