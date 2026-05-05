# api_views.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import DeviceLocation
from .serializers import DeviceLocationSerializer

@api_view(['POST'])
def update_location(request):
    serializer = DeviceLocationSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({"status": "success"})
    return Response(serializer.errors)

@api_view(['GET'])
def latest_location(request, device_id):
    try:
        data = DeviceLocation.objects.filter(device_id=device_id).latest('timestamp')
        serializer = DeviceLocationSerializer(data)
        return Response(serializer.data)
    except:
        return Response({"error": "No data"})