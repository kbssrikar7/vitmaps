# models.py
from django.db import models

class DeviceLocation(models.Model):
    device_id = models.CharField(max_length=100)

    class Meta:
        app_label = 'tracking_app'

    def __str__(self):
        return self.device_id
    
class Vessel(models.Model):
    name = models.CharField(max_length=100)
    imo = models.CharField(max_length=50)
    flag = models.CharField(max_length=50)
    operator = models.CharField(max_length=100)
    cargo = models.CharField(max_length=100)
    vessel_type = models.CharField(max_length=100)

    def __str__(self):
        return self.name