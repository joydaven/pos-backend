import React, { useState, useEffect, useCallback } from 'react';
import {
    Box,
    Typography,
    Paper,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Button,
    IconButton,
    TextField,
    TablePagination,
    Tooltip,
    Menu,
    MenuItem,
    Alert,
    TableSortLabel,
    Chip,
    Stack,
    CircularProgress,
} from '@mui/material';
import { 
    Edit, 
    Delete, 
    Sync, 
    MoreVert, 
    FileDownload, 
    FilterList,
    Search,
    Mail,
    Phone,
} from '@mui/icons-material';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ordersApi, syncApi } from '../../services/api';
import { Order, Contact } from '../../types';
import { useInterval } from '../../hooks/useInterval';

interface OrdersResponse {
    count: number;
    next: string | null;
    previous: string | null;
    results: Order[];
}

type SortOrder = 'asc' | 'desc';
type OrderBy = 'order_date' | 'total_amount' | 'status';

// Add type guard for Contact
const isContact = (contact: string | Contact): contact is Contact => {
    return typeof contact !== 'string' && contact !== null;
};

export const OrdersPage: React.FC = () => {
    const queryClient = useQueryClient();
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(10);
    const [searchTerm, setSearchTerm] = useState('');
    const [debouncedSearchTerm, setDebouncedSearchTerm] = useState(searchTerm);
    const [order, setOrder] = useState<SortOrder>('desc');
    const [orderBy, setOrderBy] = useState<OrderBy>('order_date');
    const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
    const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
    const [isSyncing, setIsSyncing] = useState(false);
    const [syncProgress, setSyncProgress] = useState<{ current: number; total: number } | null>(null);

    // Debounce search term
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedSearchTerm(searchTerm);
        }, 300); // 300ms delay

        return () => clearTimeout(timer);
    }, [searchTerm]);

    const { data: ordersData, isLoading, error } = useQuery<OrdersResponse>({
        queryKey: ['orders', page, pageSize, debouncedSearchTerm, order, orderBy],
        queryFn: async () => {
            const response = await ordersApi.getAll(page, pageSize, debouncedSearchTerm, {
                ordering: `${order === 'desc' ? '-' : ''}${orderBy}`,
            });
            return response.data;
        },
    });

    const orders = ordersData?.results || [];
    const totalOrders = ordersData?.count || 0;

    const mutation = useMutation({
        mutationFn: () => syncApi.syncWooCommerce('orders'),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['orders', page, pageSize, debouncedSearchTerm, order, orderBy] });
        },
    });

    const handleSort = (property: OrderBy) => {
        const isAsc = orderBy === property && order === 'asc';
        setOrder(isAsc ? 'desc' : 'asc');
        setOrderBy(property);
    };

    const handleChangePage = (event: unknown, newPage: number) => {
        setPage(newPage + 1); // Convert 0-based to 1-based
    };

    const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>) => {
        setPageSize(parseInt(event.target.value, 10));
        setPage(1);
    };

    const handleMenuOpen = (event: React.MouseEvent<HTMLElement>, order: Order) => {
        setAnchorEl(event.currentTarget);
        setSelectedOrder(order);
    };

    const handleMenuClose = () => {
        setAnchorEl(null);
        setSelectedOrder(null);
    };

    const handleExportCSV = () => {
        // TODO: Implement CSV export
        console.log('Exporting to CSV...');
    };

    const handleSearch = (event: React.ChangeEvent<HTMLInputElement>) => {
        setSearchTerm(event.target.value);
        setPage(1); // Reset to first page when searching
    };

    const handleSync = useCallback(async () => {
        try {
            setIsSyncing(true);
            await syncApi.syncWooCommerce('orders');
        } catch (error) {
            console.error('Error syncing orders:', error);
        }
    }, []);

    const checkSyncProgress = useCallback(async () => {
        if (!isSyncing) return;

        try {
            const status = await syncApi.getSyncProgress();
            
            if (status.status === 'in_progress' && status.progress) {
                setSyncProgress(status.progress);
            } else if (status.status === 'success' || status.status === 'error' || status.status === 'stopped') {
                setIsSyncing(false);
                setSyncProgress(null);
                queryClient.invalidateQueries({ queryKey: ['orders', page, pageSize, debouncedSearchTerm, order, orderBy] });
            }
        } catch (error) {
            console.error('Error checking sync progress:', error);
            setIsSyncing(false);
            setSyncProgress(null);
        }
    }, [isSyncing, queryClient, page, pageSize, debouncedSearchTerm, order, orderBy]);

    useInterval(checkSyncProgress, isSyncing ? 1000 : null);

    const handleStopSync = useCallback(async () => {
        try {
            await syncApi.stopWooCommerceSync();
        } catch (error) {
            console.error('Error stopping sync:', error);
        }
    }, []);

    if (isLoading) {
        return (
            <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
                <CircularProgress />
            </Box>
        );
    }

    if (error) {
        return <Alert severity="error">Error loading orders</Alert>;
    }

    return (
        <Box>
            <Box display="flex" justifyContent="space-between" alignItems="center" mb={3}>
                <Typography variant="h4">Orders</Typography>
                <Stack direction="row" spacing={2}>
                    {isSyncing && syncProgress && (
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <CircularProgress size={24} />
                            <Typography>
                                Syncing... {syncProgress.current}/{syncProgress.total}
                            </Typography>
                            <Button variant="outlined" color="secondary" onClick={handleStopSync}>
                                Stop Sync
                            </Button>
                        </Box>
                    )}
                    <Button
                        variant="contained"
                        color="primary"
                        onClick={handleSync}
                        startIcon={<Sync />}
                        disabled={isSyncing}
                    >
                        {isSyncing ? 'Syncing...' : 'Sync Orders'}
                    </Button>
                    <Button
                        variant="outlined"
                        startIcon={<FileDownload />}
                        onClick={handleExportCSV}
                    >
                        Export CSV
                    </Button>
                </Stack>
            </Box>

            {mutation.isSuccess && (
                <Alert severity="success" sx={{ mb: 2 }}>
                    Orders synchronized successfully!
                </Alert>
            )}

            {mutation.isError && (
                <Alert severity="error" sx={{ mb: 2 }}>
                    Error synchronizing orders
                </Alert>
            )}

            <Paper sx={{ mb: 2, p: 2 }}>
                <TextField
                    fullWidth
                    variant="outlined"
                    placeholder="Search orders..."
                    value={searchTerm}
                    onChange={handleSearch}
                    InputProps={{
                        startAdornment: <Search sx={{ color: 'text.secondary', mr: 1 }} />,
                    }}
                    sx={{ mb: 2 }}
                />

                <TableContainer>
                    <Table>
                        <TableHead>
                            <TableRow>
                                <TableCell>Order ID</TableCell>
                                <TableCell>
                                    <TableSortLabel
                                        active={orderBy === 'order_date'}
                                        direction={orderBy === 'order_date' ? order : 'asc'}
                                        onClick={() => handleSort('order_date')}
                                    >
                                        Date
                                    </TableSortLabel>
                                </TableCell>
                                <TableCell>Customer</TableCell>
                                <TableCell>
                                    <TableSortLabel
                                        active={orderBy === 'total_amount'}
                                        direction={orderBy === 'total_amount' ? order : 'asc'}
                                        onClick={() => handleSort('total_amount')}
                                    >
                                        Total Amount
                                    </TableSortLabel>
                                </TableCell>
                                <TableCell>
                                    <TableSortLabel
                                        active={orderBy === 'status'}
                                        direction={orderBy === 'status' ? order : 'asc'}
                                        onClick={() => handleSort('status')}
                                    >
                                        Status
                                    </TableSortLabel>
                                </TableCell>
                                <TableCell>Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {(orders || []).map((order) => (
                                <TableRow key={order.woo_order_id}>
                                    <TableCell>#{order.woo_order_id}</TableCell>
                                    <TableCell>
                                        {new Date(order.order_date).toLocaleDateString('en-US', {
                                            year: 'numeric',
                                            month: 'short',
                                            day: 'numeric'
                                        })}
                                    </TableCell>
                                    <TableCell>
                                        <Stack spacing={0.5}>
                                            <Typography variant="subtitle2">
                                                {isContact(order.contact) 
                                                    ? `${order.contact.first_name} ${order.contact.last_name}`
                                                    : order.contact}
                                            </Typography>
                                            {isContact(order.contact) && (
                                                <>
                                                    {order.contact.email && (
                                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                                                            <Mail fontSize="small" sx={{ color: 'text.secondary', fontSize: '1rem' }} />
                                                            <Typography variant="body2" color="text.secondary">
                                                                {order.contact.email}
                                                            </Typography>
                                                        </Box>
                                                    )}
                                                    {order.contact.phone && (
                                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                                                            <Phone fontSize="small" sx={{ color: 'text.secondary', fontSize: '1rem' }} />
                                                            <Typography variant="body2" color="text.secondary">
                                                                {order.contact.phone}
                                                            </Typography>
                                                        </Box>
                                                    )}
                                                </>
                                            )}
                                        </Stack>
                                    </TableCell>
                                    <TableCell>${parseFloat(order.total_amount).toFixed(2)}</TableCell>
                                    <TableCell>
                                        <Chip 
                                            label={order.status} 
                                            color={
                                                order.status === 'completed' ? 'success' :
                                                order.status === 'processing' ? 'info' :
                                                order.status === 'pending' ? 'warning' :
                                                'default'
                                            }
                                            size="small"
                                        />
                                    </TableCell>
                                    <TableCell>
                                        <IconButton
                                            size="small"
                                            onClick={(event) => handleMenuOpen(event, order)}
                                        >
                                            <MoreVert />
                                        </IconButton>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </TableContainer>

                <TablePagination
                    component="div"
                    count={totalOrders}
                    page={page - 1} // Convert 1-based to 0-based for Material-UI
                    onPageChange={handleChangePage}
                    rowsPerPage={pageSize}
                    onRowsPerPageChange={handleChangeRowsPerPage}
                    rowsPerPageOptions={[10, 25, 50]}
                />
            </Paper>

            <Menu
                anchorEl={anchorEl}
                open={Boolean(anchorEl)}
                onClose={handleMenuClose}
            >
                <MenuItem onClick={handleMenuClose}>
                    <Edit sx={{ mr: 1 }} /> View Details
                </MenuItem>
                <MenuItem onClick={handleMenuClose}>
                    <Delete sx={{ mr: 1 }} /> Cancel Order
                </MenuItem>
            </Menu>
        </Box>
    );
};

export default OrdersPage;
