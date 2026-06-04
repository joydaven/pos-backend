"""
Custom middleware for auth_api
"""

class DisableCSRFMiddleware:
    """
    Middleware to disable CSRF for specific paths
    """
    def __init__(self, get_response):
        self.get_response = get_response
        # Paths that should be exempt from CSRF
        self.csrf_exempt_paths = ['/api/auth/google/']

    def __call__(self, request):
        # Check if the path is in the exempt list
        if any(request.path.startswith(path) for path in self.csrf_exempt_paths):
            request._dont_enforce_csrf_checks = True
            
        response = self.get_response(request)
        return response
