import React, { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    Box,
    Button,
    Card,
    CardContent,
    CardMedia,
    CircularProgress,
    Grid,
    Typography,
    Alert,
    Chip,
    Stack,
    TextField,
    MenuItem,
    IconButton,
    Menu,
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    FormControl,
    InputLabel,
    Select,
    SelectChangeEvent,
    Tooltip,
    InputAdornment,
    Drawer,
    List,
    ListItem,
    ListItemText,
    ListItemIcon,
    Divider,
    Paper,
    ToggleButtonGroup,
    ToggleButton,
    TableContainer,
    Table,
    TableHead,
    TableRow,
    TableCell,
    TableBody,
    TableSortLabel,
    Pagination,
} from '@mui/material';
import {
    Refresh as RefreshIcon,
    Search as SearchIcon,
    Edit as EditIcon,
    Delete as DeleteIcon,
    FilterList as FilterIcon,
    Sort as SortIcon,
    MoreVert as MoreVertIcon,
    Inventory as InventoryIcon,
    Category as CategoryIcon,
    Timeline as TimelineIcon,
    Analytics as AnalyticsIcon,
    GridView as GridViewIcon,
    ViewList as ListViewIcon,
    Stop as StopIcon,
} from '@mui/icons-material';
import { productsApi, syncApi } from '../../services/api';
import { Product } from '../../types/product';
import { SyncResponse } from '../../types';

export const ProductsPage: React.FC = () => {
    const queryClient = useQueryClient();
    const searchInputRef = useRef<HTMLInputElement>(null);
    const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid');
    const [searchTerm, setSearchTerm] = useState('');
    const [debouncedSearchTerm, setDebouncedSearchTerm] = useState(searchTerm);
    const [categoryFilter, setCategoryFilter] = useState<string>('all');
    const [stockFilter, setStockFilter] = useState<string>('all');
    const [sortBy, setSortBy] = useState<string>('name');
    const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
    const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
    const [isAnalyticsDrawerOpen, setIsAnalyticsDrawerOpen] = useState(false);
    const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(10);
    const [syncStatus, setSyncStatus] = useState<{ status: string } | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedSearchTerm(searchTerm);
            // Keep focus on the search input after state update
            searchInputRef.current?.focus();
        }, 500);
        return () => clearTimeout(timer);
    }, [searchTerm]);

    // Query for fetching products
    const { data: productsData, isLoading, error: productsError } = useQuery({
        queryKey: ['products', page, pageSize, debouncedSearchTerm, categoryFilter, stockFilter, sortBy],
        queryFn: async () => {
            const response = await productsApi.getAll(page, pageSize, debouncedSearchTerm, {
                category: categoryFilter !== 'all' ? categoryFilter : undefined,
                stockStatus: stockFilter !== 'all' ? stockFilter : undefined,
                sortBy,
            });
            return response.data;
        },
    });

    const syncMutation = useMutation({
        mutationFn: syncApi.syncWooCommerce,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['products'] });
        },
    });

    const handleSync = async () => {
        try {
            setError(null);
            await syncApi.syncWooCommerce();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'An error occurred during sync');
        }
    };

    const handleStopSync = async () => {
        try {
            setError(null);
            await syncApi.stopWooCommerceSync();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'An error occurred while stopping sync');
        }
    };

    const handleProductAction = (event: React.MouseEvent<HTMLElement>, product: Product) => {
        setSelectedProduct(product);
        setAnchorEl(event.currentTarget);
    };

    const handleCloseMenu = () => {
        setAnchorEl(null);
    };

    const handleEditProduct = () => {
        setIsEditDialogOpen(true);
        handleCloseMenu();
    };

    const handleCloseEditDialog = () => {
        setIsEditDialogOpen(false);
        setSelectedProduct(null);
    };

    const handleShowAnalytics = () => {
        setIsAnalyticsDrawerOpen(true);
        handleCloseMenu();
    };

    const products = productsData?.results || [];

    const handlePageChange = (event: unknown, newPage: number) => {
        setPage(newPage);
    };

    const handlePageSizeChange = (event: SelectChangeEvent<number>) => {
        setPageSize(event.target.value as number);
        setPage(1);
    };

    if (isLoading) {
        return (
            <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
                <CircularProgress />
            </Box>
        );
    }

    if (productsError) {
        return <Alert severity="error">Error loading products</Alert>;
    }

    return (
        <Box sx={{ p: 3 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
                <Typography variant="h4">Products</Typography>
                <Button
                    variant="contained"
                    color={syncStatus?.status === 'in_progress' ? 'error' : 'primary'}
                    onClick={syncStatus?.status === 'in_progress' ? handleStopSync : handleSync}
                    startIcon={syncStatus?.status === 'in_progress' ? <StopIcon /> : <RefreshIcon />}
                >
                    {syncStatus?.status === 'in_progress' ? 'Stop Sync' : 'Sync with WooCommerce'}
                </Button>
            </Box>

            {syncMutation.isSuccess && (
                <Alert severity="success" sx={{ mb: 2 }}>
                    <Box>
                        Products synchronized successfully!
                        {syncMutation.data?.stats && (
                            <Typography component="span" sx={{ ml: 1 }}>
                                Synced {syncMutation.data.stats.products} products,{' '}
                                {syncMutation.data.stats.customers} customers, and{' '}
                                {syncMutation.data.stats.orders} orders.
                            </Typography>
                        )}
                    </Box>
                </Alert>
            )}

            {syncMutation.isError && (
                <Alert severity="error" sx={{ mb: 2 }}>
                    Error synchronizing products
                </Alert>
            )}

            {error && (
                <Alert severity="error" sx={{ mb: 2 }}>
                    {error}
                </Alert>
            )}

            <Paper sx={{ p: 2, mb: 3 }}>
                <Grid container spacing={2} alignItems="center">
                    <Grid item xs={12} sm={4}>
                        <TextField
                            inputRef={searchInputRef}
                            size="small"
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                            placeholder="Search products..."
                            InputProps={{
                                startAdornment: (
                                    <InputAdornment position="start">
                                        <SearchIcon />
                                    </InputAdornment>
                                ),
                            }}
                        />
                    </Grid>
                    <Grid item xs={12} sm={2}>
                        <FormControl fullWidth>
                            <InputLabel>Category</InputLabel>
                            <Select
                                value={categoryFilter}
                                onChange={(e) => setCategoryFilter(e.target.value)}
                                label="Category"
                            >
                                <MenuItem value="all">All Categories</MenuItem>
                                {Array.from(new Set(products.flatMap(product => product.categories))).map((category) => (
                                    <MenuItem key={category} value={category}>
                                        {category}
                                    </MenuItem>
                                ))}
                            </Select>
                        </FormControl>
                    </Grid>
                    <Grid item xs={12} sm={2}>
                        <FormControl fullWidth>
                            <InputLabel>Stock Status</InputLabel>
                            <Select
                                value={stockFilter}
                                onChange={(e) => setStockFilter(e.target.value)}
                                label="Stock Status"
                            >
                                <MenuItem value="all">All Stock</MenuItem>
                                <MenuItem value="instock">In Stock</MenuItem>
                                <MenuItem value="outofstock">Out of Stock</MenuItem>
                                <MenuItem value="onbackorder">On Backorder</MenuItem>
                            </Select>
                        </FormControl>
                    </Grid>
                    <Grid item xs={12} sm={2}>
                        <FormControl fullWidth>
                            <InputLabel>Sort By</InputLabel>
                            <Select
                                value={sortBy}
                                onChange={(e) => setSortBy(e.target.value)}
                                label="Sort By"
                            >
                                <MenuItem value="name">Name</MenuItem>
                                <MenuItem value="price">Price</MenuItem>
                                <MenuItem value="stock">Stock</MenuItem>
                            </Select>
                        </FormControl>
                    </Grid>
                </Grid>
            </Paper>

            {viewMode === 'grid' ? (
                <Grid container spacing={3}>
                    {products.map((product: Product) => (
                        <Grid item xs={12} sm={6} md={4} lg={3} key={product.id}>
                            <Card>
                                <CardMedia
                                    component="img"
                                    height="200"
                                    image={product.images[0] || '/placeholder-product.png'}
                                    alt={product.name}
                                    sx={{ objectFit: 'contain' }}
                                />
                                <CardContent>
                                    <Box display="flex" justifyContent="space-between" alignItems="flex-start">
                                        <Typography gutterBottom variant="h6" component="div" noWrap sx={{ flex: 1 }}>
                                            {product.name}
                                        </Typography>
                                        <IconButton size="small" onClick={(e) => handleProductAction(e, product)}>
                                            <MoreVertIcon />
                                        </IconButton>
                                    </Box>
                                    <Stack direction="row" spacing={1} mb={1} flexWrap="wrap">
                                        <Chip
                                            label={product.stock_status}
                                            color={product.stock_status === 'instock' ? 'success' : 'error'}
                                            size="small"
                                        />
                                        {product.categories.map((category: string, index: number) => (
                                            <Chip key={index} label={category} size="small" />
                                        ))}
                                    </Stack>
                                    <Typography variant="body2" color="text.secondary" mb={1}>
                                        {product.description.replace(/<[^>]*>/g, '').substring(0, 100)}...
                                    </Typography>
                                    <Box display="flex" justifyContent="space-between" alignItems="center">
                                        <Box>
                                            <Typography variant="h6" color="primary">
                                                ${parseFloat(product.price).toFixed(2)}
                                            </Typography>
                                            {product.sale_price && (
                                                <Typography variant="body2" color="error">
                                                    Sale: ${parseFloat(product.sale_price).toFixed(2)}
                                                </Typography>
                                            )}
                                        </Box>
                                        <Typography variant="body2" color="text.secondary">
                                            Stock: {product.stock_quantity || 0}
                                        </Typography>
                                    </Box>
                                </CardContent>
                            </Card>
                        </Grid>
                    ))}
                </Grid>
            ) : (
                <TableContainer component={Paper}>
                    <Table>
                        <TableHead>
                            <TableRow>
                                <TableCell>Image</TableCell>
                                <TableCell>
                                    <TableSortLabel
                                        active={sortBy === 'name'}
                                        direction={sortBy === 'name' ? 'asc' : 'asc'}
                                        onClick={() => setSortBy('name')}
                                    >
                                        Name
                                    </TableSortLabel>
                                </TableCell>
                                <TableCell>
                                    <TableSortLabel
                                        active={sortBy === 'price'}
                                        direction={sortBy === 'price' ? 'asc' : 'asc'}
                                        onClick={() => setSortBy('price')}
                                    >
                                        Price
                                    </TableSortLabel>
                                </TableCell>
                                <TableCell>
                                    <TableSortLabel
                                        active={sortBy === 'stock'}
                                        direction={sortBy === 'stock' ? 'asc' : 'asc'}
                                        onClick={() => setSortBy('stock')}
                                    >
                                        Stock
                                    </TableSortLabel>
                                </TableCell>
                                <TableCell>Categories</TableCell>
                                <TableCell>Status</TableCell>
                                <TableCell align="right">Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {products.map((product: Product) => (
                                <TableRow key={product.id}>
                                    <TableCell>
                                        <Box
                                            component="img"
                                            src={product.images[0] || '/placeholder-product.png'}
                                            alt={product.name}
                                            sx={{ width: 50, height: 50, objectFit: 'contain' }}
                                        />
                                    </TableCell>
                                    <TableCell>{product.name}</TableCell>
                                    <TableCell>
                                        <Stack>
                                            <Typography>${parseFloat(product.price).toFixed(2)}</Typography>
                                            {product.sale_price && (
                                                <Typography variant="body2" color="error">
                                                    Sale: ${parseFloat(product.sale_price).toFixed(2)}
                                                </Typography>
                                            )}
                                        </Stack>
                                    </TableCell>
                                    <TableCell>
                                        <Stack direction="row" spacing={1} alignItems="center">
                                            <Typography>{product.stock_quantity || 0}</Typography>
                                            <Chip
                                                label={product.stock_status}
                                                color={product.stock_status === 'instock' ? 'success' : 'error'}
                                                size="small"
                                            />
                                        </Stack>
                                    </TableCell>
                                    <TableCell>
                                        <Stack direction="row" spacing={0.5}>
                                            {product.categories.map((category: string, index: number) => (
                                                <Chip key={index} label={category} size="small" />
                                            ))}
                                        </Stack>
                                    </TableCell>
                                    <TableCell>
                                        <Chip
                                            label={product.status}
                                            color={product.status === 'publish' ? 'success' : 'default'}
                                            size="small"
                                        />
                                    </TableCell>
                                    <TableCell align="right">
                                        <IconButton size="small" onClick={(e) => handleProductAction(e, product)}>
                                            <MoreVertIcon />
                                        </IconButton>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </TableContainer>
            )}

            <Box display="flex" justifyContent="flex-end" mt={2}>
                <Stack spacing={2} direction="row" alignItems="center">
                    <Typography variant="body2">Rows per page:</Typography>
                    <Select
                        value={pageSize}
                        onChange={handlePageSizeChange}
                        size="small"
                    >
                        <MenuItem value={10}>10</MenuItem>
                        <MenuItem value={25}>25</MenuItem>
                        <MenuItem value={50}>50</MenuItem>
                    </Select>
                    <Pagination
                        count={Math.ceil((productsData?.count || 0) / pageSize)}
                        page={page}
                        onChange={handlePageChange}
                        color="primary"
                    />
                </Stack>
            </Box>

            <Menu
                anchorEl={anchorEl}
                open={Boolean(anchorEl)}
                onClose={handleCloseMenu}
            >
                <MenuItem onClick={handleEditProduct}>
                    <ListItemIcon>
                        <EditIcon fontSize="small" />
                    </ListItemIcon>
                    <ListItemText>Edit Product</ListItemText>
                </MenuItem>
                <MenuItem onClick={handleShowAnalytics}>
                    <ListItemIcon>
                        <AnalyticsIcon fontSize="small" />
                    </ListItemIcon>
                    <ListItemText>View Analytics</ListItemText>
                </MenuItem>
                <MenuItem>
                    <ListItemIcon>
                        <InventoryIcon fontSize="small" />
                    </ListItemIcon>
                    <ListItemText>Update Stock</ListItemText>
                </MenuItem>
                <Divider />
                <MenuItem sx={{ color: 'error.main' }}>
                    <ListItemIcon>
                        <DeleteIcon fontSize="small" color="error" />
                    </ListItemIcon>
                    <ListItemText>Delete Product</ListItemText>
                </MenuItem>
            </Menu>

            <Dialog open={isEditDialogOpen} onClose={handleCloseEditDialog} maxWidth="md" fullWidth>
                <DialogTitle>Edit Product</DialogTitle>
                <DialogContent>
                    {selectedProduct && (
                        <Grid container spacing={2} sx={{ mt: 1 }}>
                            <Grid item xs={12}>
                                <TextField
                                    fullWidth
                                    label="Product Name"
                                    defaultValue={selectedProduct.name}
                                />
                            </Grid>
                            <Grid item xs={12}>
                                <TextField
                                    fullWidth
                                    label="Description"
                                    multiline
                                    rows={4}
                                    defaultValue={selectedProduct.description.replace(/<[^>]*>/g, '')}
                                />
                            </Grid>
                            <Grid item xs={6}>
                                <TextField
                                    fullWidth
                                    label="Regular Price"
                                    type="number"
                                    defaultValue={selectedProduct.price}
                                    InputProps={{
                                        startAdornment: <InputAdornment position="start">$</InputAdornment>,
                                    }}
                                />
                            </Grid>
                            <Grid item xs={6}>
                                <TextField
                                    fullWidth
                                    label="Sale Price"
                                    type="number"
                                    defaultValue={selectedProduct.sale_price}
                                    InputProps={{
                                        startAdornment: <InputAdornment position="start">$</InputAdornment>,
                                    }}
                                />
                            </Grid>
                            <Grid item xs={6}>
                                <TextField
                                    fullWidth
                                    label="Stock Quantity"
                                    type="number"
                                    defaultValue={selectedProduct.stock_quantity}
                                />
                            </Grid>
                            <Grid item xs={6}>
                                <FormControl fullWidth>
                                    <InputLabel>Stock Status</InputLabel>
                                    <Select
                                        defaultValue={selectedProduct.stock_status}
                                        label="Stock Status"
                                    >
                                        <MenuItem value="instock">In Stock</MenuItem>
                                        <MenuItem value="outofstock">Out of Stock</MenuItem>
                                        <MenuItem value="onbackorder">On Backorder</MenuItem>
                                    </Select>
                                </FormControl>
                            </Grid>
                        </Grid>
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={handleCloseEditDialog}>Cancel</Button>
                    <Button variant="contained" color="primary">Save Changes</Button>
                </DialogActions>
            </Dialog>

            <Drawer
                anchor="right"
                open={isAnalyticsDrawerOpen}
                onClose={() => setIsAnalyticsDrawerOpen(false)}
                sx={{ '& .MuiDrawer-paper': { width: { xs: '100%', sm: 400 } } }}
            >
                <Box sx={{ p: 3 }}>
                    <Typography variant="h6" gutterBottom>
                        Product Analytics
                    </Typography>
                    {selectedProduct && (
                        <>
                            <Typography variant="subtitle1" gutterBottom>
                                {selectedProduct.name}
                            </Typography>
                            <List>
                                <ListItem>
                                    <ListItemIcon>
                                        <TimelineIcon />
                                    </ListItemIcon>
                                    <ListItemText 
                                        primary="Total Sales" 
                                        secondary="71 units ($1,420)"
                                    />
                                </ListItem>
                                <ListItem>
                                    <ListItemIcon>
                                        <InventoryIcon />
                                    </ListItemIcon>
                                    <ListItemText 
                                        primary="Current Stock" 
                                        secondary={`${selectedProduct.stock_quantity || 0} units`}
                                    />
                                </ListItem>
                                <ListItem>
                                    <ListItemIcon>
                                        <CategoryIcon />
                                    </ListItemIcon>
                                    <ListItemText 
                                        primary="Categories" 
                                        secondary={selectedProduct.categories.join(', ')}
                                    />
                                </ListItem>
                            </List>
                        </>
                    )}
                </Box>
            </Drawer>
        </Box>
    );
};

export default ProductsPage;
