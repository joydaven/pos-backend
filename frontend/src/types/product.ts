export interface Product {
    id: string;
    woo_product_id: number;
    name: string;
    description: string;
    price: string;
    regular_price: string;
    sale_price: string | null;
    status: string;
    stock_status: string;
    stock_quantity: number | null;
    categories: string[];
    images: string[];
    created_at: string;
    updated_at: string;
}

export interface ProductsResponse {
    count: number;
    next: string | null;
    previous: string | null;
    results: Product[];
}
