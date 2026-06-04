from django.http import JsonResponse
from django.db import connection
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from psycopg2 import sql
import json
import logging

logger = logging.getLogger(__name__)


def _get_valid_table_names():
    """Return the set of public table names from information_schema (whitelist)."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        return {row[0] for row in cur.fetchall()}

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_database_schema(request):
    """
    Get complete database schema including table names, row counts, and structure.
    This provides real-time information about the PostgreSQL database.
    """
    try:
        with connection.cursor() as cursor:
            # Get all tables with row counts
            cursor.execute("""
                SELECT 
                    table_name,
                    (
                        SELECT COUNT(*) 
                        FROM information_schema.tables t2 
                        WHERE t2.table_name = t.table_name 
                        AND t2.table_schema = 'public'
                    ) as exists_check
                FROM information_schema.tables t
                WHERE table_schema = 'public' 
                AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            
            tables_basic = cursor.fetchall()
            
            # Get row counts for each table (this might be slow for large tables)
            tables_with_counts = []
            
            for table_name, exists_check in tables_basic:
                try:
                    # Get row count for each table
                    cursor.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table_name)))
                    row_count = cursor.fetchone()[0]
                    
                    # Get column information
                    cursor.execute("""
                        SELECT 
                            column_name,
                            data_type,
                            is_nullable,
                            column_default
                        FROM information_schema.columns 
                        WHERE table_name = %s 
                        AND table_schema = 'public'
                        ORDER BY ordinal_position
                    """, [table_name])
                    
                    columns = []
                    for col_name, data_type, is_nullable, col_default in cursor.fetchall():
                        columns.append({
                            'name': col_name,
                            'type': data_type,
                            'nullable': is_nullable == 'YES',
                            'default': col_default
                        })
                    
                    tables_with_counts.append({
                        'name': table_name,
                        'row_count': row_count,
                        'columns': columns,
                        'category': categorize_table(table_name)
                    })
                    
                except Exception as e:
                    # If we can't get row count (permissions, etc.), still include the table
                    tables_with_counts.append({
                        'name': table_name,
                        'row_count': 'N/A',
                        'columns': [],
                        'category': categorize_table(table_name),
                        'error': str(e)
                    })
            
            # Get database statistics
            cursor.execute("""
                SELECT 
                    pg_database.datname as database_name,
                    pg_size_pretty(pg_database_size(pg_database.datname)) as size
                FROM pg_database 
                WHERE datname = current_database()
            """)
            
            db_info = cursor.fetchone()
            
            # Get current timestamp
            cursor.execute("SELECT NOW()")
            timestamp = cursor.fetchone()[0]
            
            response_data = {
                'database': {
                    'name': db_info[0] if db_info else 'unknown',
                    'size': db_info[1] if db_info else 'unknown',
                    'engine': 'PostgreSQL',
                    'table_count': len(tables_with_counts)
                },
                'tables': tables_with_counts,
                'categories': {
                    'crm': [t for t in tables_with_counts if t['category'] == 'crm'],
                    'auth': [t for t in tables_with_counts if t['category'] == 'auth'],
                    'oauth2': [t for t in tables_with_counts if t['category'] == 'oauth2'],
                    'payments': [t for t in tables_with_counts if t['category'] == 'payments'],
                    'shipping': [t for t in tables_with_counts if t['category'] == 'shipping'],
                    'django': [t for t in tables_with_counts if t['category'] == 'django'],
                    'other': [t for t in tables_with_counts if t['category'] == 'other']
                },
                'timestamp': timestamp.isoformat() if timestamp else None
            }
            
            return JsonResponse(response_data)
            
    except Exception as e:
        logger.error(f"Database schema fetch error: {str(e)}", exc_info=True)
        return JsonResponse({
            'error': 'Failed to fetch database schema',
            'message': str(e),
            'tables': [],
            'database': {}
        }, status=500)


def categorize_table(table_name):
    """
    Categorize tables by their prefix/purpose
    """
    if table_name.startswith('crm_'):
        return 'crm'
    elif table_name.startswith('auth_'):
        return 'auth'
    elif table_name.startswith('oauth2_'):
        return 'oauth2'
    elif table_name.startswith('payments_'):
        return 'payments'
    elif table_name.startswith('shipping_'):
        return 'shipping'
    elif table_name.startswith('django_'):
        return 'django'
    elif table_name in ['woo_credited_services', 'woo_service_types', 'saved_carts', 'users_userprofile']:
        return 'crm'  # Related to CRM functionality
    else:
        return 'other'


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_table_detail(request, table_name):
    """
    Get detailed information about a specific table
    """
    # Validate table_name against whitelist to prevent SQL injection
    if table_name not in _get_valid_table_names():
        return JsonResponse({'error': f'Table not found: {table_name}'}, status=404)

    try:
        with connection.cursor() as cursor:
            # Get table structure
            cursor.execute("""
                SELECT 
                    column_name,
                    data_type,
                    character_maximum_length,
                    is_nullable,
                    column_default,
                    ordinal_position
                FROM information_schema.columns 
                WHERE table_name = %s 
                AND table_schema = 'public'
                ORDER BY ordinal_position
            """, [table_name])
            
            columns = []
            for row in cursor.fetchall():
                columns.append({
                    'name': row[0],
                    'type': row[1],
                    'max_length': row[2],
                    'nullable': row[3] == 'YES',
                    'default': row[4],
                    'position': row[5]
                })
            
            # Get constraints (primary keys, foreign keys, etc.)
            cursor.execute("""
                SELECT
                    tc.constraint_name,
                    tc.constraint_type,
                    kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu 
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = %s
                AND tc.table_schema = 'public'
            """, [table_name])
            
            constraints = []
            for row in cursor.fetchall():
                constraints.append({
                    'name': row[0],
                    'type': row[1],
                    'column': row[2]
                })
            
            # Get row count
            cursor.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table_name)))
            row_count = cursor.fetchone()[0]
            
            return JsonResponse({
                'table_name': table_name,
                'row_count': row_count,
                'columns': columns,
                'constraints': constraints,
                'category': categorize_table(table_name)
            })
            
    except Exception as e:
        return JsonResponse({
            'error': f'Failed to fetch table details for {table_name}',
            'message': str(e)
        }, status=500)
