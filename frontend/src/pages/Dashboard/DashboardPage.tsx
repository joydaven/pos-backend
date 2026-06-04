import React from 'react';
import { Box, Grid, Paper, Typography, Alert, CircularProgress } from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { contactsApi, ordersApi } from '../../services/api';
import { Contact, Order } from '../../types';

interface ContactsResponse {
    count: number;
    next: string | null;
    previous: string | null;
    results: Contact[];
}

interface OrdersResponse {
    count: number;
    next: string | null;
    previous: string | null;
    results: Order[];
}

export const DashboardPage: React.FC = () => {
    // Query for fetching contacts
    const { data: contactsData, isLoading: isLoadingContacts, error: contactsError } = useQuery<ContactsResponse>({
        queryKey: ['contacts'],
        queryFn: async () => {
            const response = await contactsApi.getAll();
            return response.data;
        },
    });

    const { data: ordersData, isLoading: isLoadingOrders, error: ordersError } = useQuery<OrdersResponse>({
        queryKey: ['orders'],
        queryFn: async () => {
            const response = await ordersApi.getAll();
            return response.data;
        },
    });

    if (isLoadingContacts || isLoadingOrders) {
        return (
            <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
                <CircularProgress />
            </Box>
        );
    }

    if (contactsError || ordersError) {
        return <Alert severity="error">Error loading dashboard data</Alert>;
    }

    const contacts = contactsData?.results || [];
    const totalContacts = contactsData?.count || 0;
    const totalOrders = ordersData?.count || 0;
    const orders = ordersData?.results || [];
    const totalRevenue = orders.reduce((sum, order) => sum + parseFloat(order.total_amount), 0);

    return (
        <Box>
            <Typography variant="h4" sx={{ mb: 4 }}>Dashboard</Typography>
            <Grid container spacing={3}>
                <Grid item xs={12} md={4}>
                    <Paper sx={{ p: 3, textAlign: 'center' }}>
                        <Typography variant="h6" color="primary">Total Contacts</Typography>
                        <Typography variant="h3">{totalContacts}</Typography>
                    </Paper>
                </Grid>
                <Grid item xs={12} md={4}>
                    <Paper sx={{ p: 3, textAlign: 'center' }}>
                        <Typography variant="h6" color="primary">Total Orders</Typography>
                        <Typography variant="h3">{totalOrders}</Typography>
                    </Paper>
                </Grid>
                <Grid item xs={12} md={4}>
                    <Paper sx={{ p: 3, textAlign: 'center' }}>
                        <Typography variant="h6" color="primary">Total Revenue</Typography>
                        <Typography variant="h3">${totalRevenue.toFixed(2)}</Typography>
                    </Paper>
                </Grid>
                <Grid item xs={12}>
                    <Paper sx={{ p: 3 }}>
                        <Typography variant="h6" sx={{ mb: 2 }}>Recent Orders</Typography>
                        {orders.length > 0 ? (
                            orders.slice(0, 5).map((order) => (
                                <Box key={order.id} sx={{ mb: 2, p: 2, bgcolor: 'background.default' }}>
                                    <Typography>
                                        Order #{order.woo_order_id} - ${parseFloat(order.total_amount).toFixed(2)}
                                    </Typography>
                                    <Typography variant="body2" color="text.secondary">
                                        {new Date(order.order_date).toLocaleDateString()}
                                    </Typography>
                                </Box>
                            ))
                        ) : (
                            <Typography color="text.secondary">No orders yet</Typography>
                        )}
                    </Paper>
                </Grid>
            </Grid>
        </Box>
    );
};

export default DashboardPage;
