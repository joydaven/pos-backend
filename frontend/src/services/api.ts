import axios from 'axios';
import { Contact, Order, SyncResponse } from '../types';
import { Product, ProductsResponse } from '../types/product';

interface OrdersResponse {
    count: number;
    next: string | null;
    previous: string | null;
    results: Order[];
}

interface ContactsResponse {
    count: number;
    next: string | null;
    previous: string | null;
    results: Contact[];
}

export interface SyncStatus {
  status: 'success' | 'error' | 'in_progress' | 'idle' | 'stopped';
  message: string;
  progress?: {
    current: number;
    total: number;
    type: 'products' | 'customers' | 'orders';
  };
  stats?: {
    products: number;
    customers: number;
    orders: number;
  };
}

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api';

const api = axios.create({
    baseURL: API_URL,
    headers: {
        'Content-Type': 'application/json',
    },
});

// Add token to requests if it exists
api.interceptors.request.use((config) => {
    const token = localStorage.getItem('token');
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

// Add response interceptor
api.interceptors.response.use(
    response => response,
    error => {
        console.error('API Error:', error.response || error);
        return Promise.reject(error);
    }
);

export const contactsApi = {
    getAll: async (page = 1, pageSize = 10, search = '', options?: { ordering?: string, has_woo?: string, has_ghl?: string, has_crm?: boolean }) => {
        let url = `/contacts/?page=${page}&page_size=${pageSize}`;
        
        if (search) {
            url += `&search=${encodeURIComponent(search)}`;
        }
        
        if (options?.ordering) {
            url += `&ordering=${options.ordering}`;
        }
        
        if (options?.has_woo) {
            url += `&has_woo=${options.has_woo}`;
        }
        
        if (options?.has_ghl) {
            url += `&has_ghl=${options.has_ghl}`;
        }
        
        if (options?.has_crm) {
            url += `&has_woo=false&has_ghl=false`;
        }
        
        const response = await api.get<ContactsResponse>(url);
        return response;
    },
    getAllNoPagination: async () => {
        const response = await api.get<Contact[]>('/contacts/all/');
        return response;
    },
    getOne: async (id: string) => {
        const response = await api.get<Contact>(`/contacts/${id}/`);
        return response;
    },
    create: async (data: Partial<Contact>) => {
        const response = await api.post<Contact>('/contacts/', data);
        return response;
    },
    update: async (id: string, data: Partial<Contact>) => {
        const response = await api.put<Contact>(`/contacts/${id}/`, data);
        return response;
    },
    delete: async (id: string) => {
        const response = await api.delete(`/contacts/${id}/`);
        return response;
    },
    getSourceCounts: async () => {
        const response = await api.get('/contacts/source-counts/');
        return response;
    }
};

export const ordersApi = {
    getAll: async (page = 1, pageSize = 10, search = '', options?: { ordering?: string }) => {
        const params = new URLSearchParams({
            page: page.toString(),
            page_size: pageSize.toString(),
            ...(search && { search }),
            ...(options?.ordering && { ordering: options.ordering }),
        });
        const response = await api.get<OrdersResponse>(`/orders/?${params}`);
        return response;
    },
    getAllNoPagination: () => api.get<Order[]>('/orders/all/'),
    getOne: (id: string) => api.get<Order>(`/orders/${id}/`),
};

export const productsApi = {
    getAll: async (page = 1, pageSize = 10, search = '', filters?: { category?: string; stockStatus?: string; sortBy?: string }) => {
        const params = new URLSearchParams({
            page: page.toString(),
            page_size: pageSize.toString(),
            ...(search && { search }),
            ...(filters?.category && { category: filters.category }),
            ...(filters?.stockStatus && { stock_status: filters.stockStatus }),
            ...(filters?.sortBy && { ordering: filters.sortBy }),
        });
        const response = await api.get<ProductsResponse>(`/products/?${params}`);
        return response;
    },
    getOne: async (id: string) => {
        const response = await api.get<Product>(`/products/${id}/`);
        return response;
    },
};

class ApiService {
  private baseUrl = API_URL;

  syncWooCommerce = async (type?: 'products' | 'customers' | 'orders'): Promise<SyncStatus> => {
    try {
      const response = await api.post<SyncStatus>('/sync/', { type: type });
      return {
        status: response.data.status || 'success',
        message: response.data.message || 'Sync completed successfully',
        progress: response.data.progress,
        stats: response.data.stats,
      };
    } catch (error) {
      console.error('Sync error:', error);
      return {
        status: 'error',
        message: error instanceof Error ? error.message : 'Failed to sync with WooCommerce',
      };
    }
  };

  // Alias for backward compatibility
  syncWooCommerceData = this.syncWooCommerce;

  stopWooCommerceSync = async (): Promise<void> => {
    await axios.post(`${API_URL}/crm/sync/stop/`);
  };

  getSyncProgress = async (): Promise<SyncStatus> => {
    try {
      const response = await api.get<SyncStatus>('/sync/status/');
      return response.data;
    } catch (error) {
      console.error('Error getting sync progress:', error);
      return {
        status: 'error',
        message: error instanceof Error ? error.message : 'Failed to get sync progress',
      };
    }
  };
}

export const syncApi = new ApiService();
