from rest_framework import serializers


class UpdateEmployeeUUIDSerializer(serializers.Serializer):
    employee_no = serializers.IntegerField()
    employee_uuid = serializers.CharField(max_length=36)
    device_uuid = serializers.CharField(max_length=36, required=False, allow_blank=True) 
    by_staff_id = serializers.IntegerField(required=False)  # Optional, if update is done by an admin/staff user
     

