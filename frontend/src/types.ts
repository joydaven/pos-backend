export interface Contact {
    id: number;
    first_name: string;
    last_name: string;
    email: string;
    phone: string;
    billing_address: string;
    billing_city: string;
    billing_state: string;
    billing_postcode: string;
    created_at: string;
    updated_at: string;
    has_woo?: boolean;
    has_ghl?: boolean;
}

export interface Order {
    id: number;
    woo_order_id: string;
    order_date: string;
    total_amount: string;
    status: string;
    contact: Contact | string;
    created_at: string;
    updated_at: string;
}

export interface Product {
    id: number;
    woo_product_id: string;
    name: string;
    sku: string;
    price: string;
    regular_price: string;
    stock_status: string;
    stock_quantity: number;
    created_at: string;
    updated_at: string;
}

export interface SyncResponse {
    status: 'success' | 'error' | 'in_progress' | 'idle' | 'stopped';
    message: string;
    progress?: {
        current: number;
        total: number;
        type: 'products' | 'customers' | 'orders';
    };
}
