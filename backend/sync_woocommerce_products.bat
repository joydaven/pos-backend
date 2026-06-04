@echo off
echo Starting WooCommerce Product Sync...
cd %~dp0
call venv\Scripts\activate
python manage.py sync_woocommerce_products %*
echo Sync completed.
pause
