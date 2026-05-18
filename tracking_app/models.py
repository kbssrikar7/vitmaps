# models.py
from django.db import models

class DeviceLocation(models.Model):
    device_id = models.CharField(max_length=100)

    class Meta:
        app_label = 'tracking_app'

    def __str__(self):
        return self.device_id