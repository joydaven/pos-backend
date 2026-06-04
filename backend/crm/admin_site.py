from django.contrib.admin import AdminSite

class CRMAdminSite(AdminSite):
    site_header = 'Doctors Studio CRM'
    site_title = 'Doctors Studio CRM'
    index_title = 'CRM Administration'
    
crm_admin_site = CRMAdminSite(name='crm_admin')
