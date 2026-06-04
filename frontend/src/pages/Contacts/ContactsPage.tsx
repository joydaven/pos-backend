import React, { useState, useEffect } from 'react';
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
    FormControlLabel,
    Checkbox,
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
import { contactsApi, syncApi } from '../../services/api';
import { Contact } from '../../types';

interface ContactsResponse {
    count: number;
    next: string | null;
    previous: string | null;
    results: Contact[];
}

type Order = 'asc' | 'desc';
type OrderBy = 'first_name' | 'last_name' | 'email' | 'phone';

export const ContactsPage: React.FC = () => {
    const queryClient = useQueryClient();
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(10);
    const [searchTerm, setSearchTerm] = useState('');
    const [debouncedSearchTerm, setDebouncedSearchTerm] = useState(searchTerm);
    const [orderBy, setOrderBy] = useState<OrderBy>('last_name');
    const [order, setOrder] = useState<Order>('asc');
    const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
    const [selectedContact, setSelectedContact] = useState<Contact | null>(null);
    const [showWooOnly, setShowWooOnly] = useState(false);
    const [showGhlOnly, setShowGhlOnly] = useState(false);
    const [showCrmOnly, setShowCrmOnly] = useState(false);
    const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
    const [sourceCounts, setSourceCounts] = useState({
        woo: 0,
        ghl: 0,
        crm: 0,
        total: 0
    });

    // Debounce search term
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedSearchTerm(searchTerm);
        }, 500);
        return () => clearTimeout(timer);
    }, [searchTerm]);

    // Fetch source counts
    useEffect(() => {
        const fetchSourceCounts = async () => {
            try {
                const response = await contactsApi.getSourceCounts();
                if (response.status === 200) {
                    const data = response.data;
                    setSourceCounts({
                        woo: data.woo || 0,
                        ghl: data.ghl || 0,
                        crm: data.crm_only || 0,
                        total: data.total || 0
                    });
                }
            } catch (error) {
                console.error('Error fetching source counts:', error);
            }
        };
        
        fetchSourceCounts();
    }, []);

    const { data: contactsData, isLoading, error } = useQuery<ContactsResponse>({
        queryKey: ['contacts', page, pageSize, debouncedSearchTerm, orderBy, order, showWooOnly, showGhlOnly, showCrmOnly],
        queryFn: async () => {
            const params: any = {
                ordering: `${order === 'desc' ? '-' : ''}${orderBy}`,
            };
            
            // Build filter parameters based on selected filters
            // This approach allows for selecting multiple data sources
            const hasWooParam = showWooOnly ? 'true' : undefined;
            const hasGhlParam = showGhlOnly ? 'true' : undefined;
            
            // Only apply CRM-only filter if it's the only one selected
            if (showCrmOnly && !showWooOnly && !showGhlOnly) {
                params.has_woo = 'false';
                params.has_ghl = 'false';
            } else {
                // Otherwise, apply individual filters
                if (hasWooParam) params.has_woo = hasWooParam;
                if (hasGhlParam) params.has_ghl = hasGhlParam;
            }
            
            const response = await contactsApi.getAll(page, pageSize, debouncedSearchTerm, params);
            return response.data;
        },
    });

    const contacts = contactsData?.results || [];
    const totalContacts = contactsData?.count || 0;

    const mutation = useMutation({
        mutationFn: () => syncApi.syncWooCommerce('customers'),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['contacts', page, pageSize, debouncedSearchTerm, orderBy, order, showWooOnly, showGhlOnly, showCrmOnly] });
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

    const handleMenuOpen = (event: React.MouseEvent<HTMLElement>, contact: Contact) => {
        setAnchorEl(event.currentTarget);
        setSelectedContact(contact);
    };

    const handleMenuClose = () => {
        setAnchorEl(null);
        setSelectedContact(null);
    };

    const handleExportCSV = () => {
        // TODO: Implement CSV export
        console.log('Exporting to CSV...');
    };

    const handleSearch = (event: React.ChangeEvent<HTMLInputElement>) => {
        setSearchTerm(event.target.value);
        setPage(1); // Reset to first page when searching
    };

    const handleFilterChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        const { name, checked } = event.target;
        
        if (name === 'showWooOnly') {
            setShowWooOnly(checked);
        } else if (name === 'showGhlOnly') {
            setShowGhlOnly(checked);
        } else if (name === 'showCrmOnly') {
            setShowCrmOnly(checked);
        } else if (name === 'showAll') {
            // Reset all filters
            setShowWooOnly(false);
            setShowGhlOnly(false);
            setShowCrmOnly(false);
        }
    };

    if (isLoading) {
        return (
            <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
                <CircularProgress />
            </Box>
        );
    }

    if (error) {
        return <Alert severity="error">Error loading contacts</Alert>;
    }

    return (
        <Box>
            <Box display="flex" justifyContent="space-between" alignItems="center" mb={3}>
                <Typography variant="h4">
                    Contacts 
                    <Typography component="span" variant="subtitle1" sx={{ ml: 2, color: 'text.secondary' }}>
                        ({sourceCounts.total} total)
                    </Typography>
                </Typography>
                <Stack direction="row" spacing={2}>
                    <Button
                        variant="contained"
                        color="primary"
                        onClick={() => mutation.mutate()}
                        startIcon={<Sync />}
                        disabled={mutation.isPending}
                    >
                        {mutation.isPending ? 'Syncing...' : 'Sync Contacts'}
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
                    Contacts synchronized successfully!
                </Alert>
            )}

            {mutation.isError && (
                <Alert severity="error" sx={{ mb: 2 }}>
                    Error synchronizing contacts
                </Alert>
            )}

            <Paper sx={{ mb: 2, p: 2 }}>
                <Typography variant="subtitle1" sx={{ mb: 2 }}>
                    Filter by Data Source 
                    <Typography component="span" variant="caption" sx={{ ml: 1, color: 'text.secondary' }}>
                        (Multiple sources can be selected)
                    </Typography>
                </Typography>
                <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
                    <FormControlLabel
                        control={
                            <Checkbox
                                checked={showWooOnly}
                                onChange={handleFilterChange}
                                name="showWooOnly"
                                color="primary"
                            />
                        }
                        label={
                            <Stack direction="row" spacing={1} alignItems="center">
                                <Chip label="WooCommerce" size="small" color="primary" variant="outlined" />
                                <Typography sx={{ fontSize: 12, color: 'text.secondary' }}>{sourceCounts.woo}</Typography>
                            </Stack>
                        }
                    />
                    <FormControlLabel
                        control={
                            <Checkbox
                                checked={showGhlOnly}
                                onChange={handleFilterChange}
                                name="showGhlOnly"
                                color="secondary"
                            />
                        }
                        label={
                            <Stack direction="row" spacing={1} alignItems="center">
                                <Chip label="GoHighLevel" size="small" color="secondary" variant="outlined" />
                                <Typography sx={{ fontSize: 12, color: 'text.secondary' }}>{sourceCounts.ghl}</Typography>
                            </Stack>
                        }
                    />
                    <FormControlLabel
                        control={
                            <Checkbox
                                checked={showCrmOnly}
                                onChange={handleFilterChange}
                                name="showCrmOnly"
                            />
                        }
                        label={
                            <Stack direction="row" spacing={1} alignItems="center">
                                <Chip label="CRM Only" size="small" color="default" variant="outlined" />
                                <Typography sx={{ fontSize: 12, color: 'text.secondary' }}>{sourceCounts.crm}</Typography>
                            </Stack>
                        }
                    />
                    <FormControlLabel
                        control={
                            <Checkbox
                                checked={!showWooOnly && !showGhlOnly && !showCrmOnly}
                                onChange={handleFilterChange}
                                name="showAll"
                            />
                        }
                        label={
                            <Stack direction="row" spacing={1} alignItems="center">
                                <Chip label="Show All Sources" size="small" color="default" variant="outlined" />
                                <Typography sx={{ fontSize: 12, color: 'text.secondary' }}>{sourceCounts.total}</Typography>
                            </Stack>
                        }
                    />
                </Stack>

                <TextField
                    fullWidth
                    variant="outlined"
                    placeholder="Search contacts..."
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
                                <TableCell>
                                    <TableSortLabel
                                        active={orderBy === 'first_name'}
                                        direction={orderBy === 'first_name' ? order : 'asc'}
                                        onClick={() => handleSort('first_name')}
                                    >
                                        First Name
                                    </TableSortLabel>
                                </TableCell>
                                <TableCell>
                                    <TableSortLabel
                                        active={orderBy === 'last_name'}
                                        direction={orderBy === 'last_name' ? order : 'asc'}
                                        onClick={() => handleSort('last_name')}
                                    >
                                        Last Name
                                    </TableSortLabel>
                                </TableCell>
                                <TableCell>
                                    <TableSortLabel
                                        active={orderBy === 'email'}
                                        direction={orderBy === 'email' ? order : 'asc'}
                                        onClick={() => handleSort('email')}
                                    >
                                        Email
                                    </TableSortLabel>
                                </TableCell>
                                <TableCell>
                                    <TableSortLabel
                                        active={orderBy === 'phone'}
                                        direction={orderBy === 'phone' ? order : 'asc'}
                                        onClick={() => handleSort('phone')}
                                    >
                                        Phone
                                    </TableSortLabel>
                                </TableCell>
                                <TableCell>Address</TableCell>
                                <TableCell>City</TableCell>
                                <TableCell>State</TableCell>
                                <TableCell>Postal Code</TableCell>
                                <TableCell>Data Sources</TableCell>
                                <TableCell>Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {(contacts || []).map((contact) => (
                                <TableRow key={contact.id}>
                                    <TableCell>{contact.first_name}</TableCell>
                                    <TableCell>{contact.last_name}</TableCell>
                                    <TableCell>
                                        <Stack direction="row" spacing={1} alignItems="center">
                                            <Typography>{contact.email}</Typography>
                                            <IconButton
                                                size="small"
                                                onClick={() => window.location.href = `mailto:${contact.email}`}
                                            >
                                                <Mail fontSize="small" />
                                            </IconButton>
                                        </Stack>
                                    </TableCell>
                                    <TableCell>
                                        {contact.phone && (
                                            <Stack direction="row" spacing={1} alignItems="center">
                                                <Typography>{contact.phone}</Typography>
                                                <IconButton
                                                    size="small"
                                                    onClick={() => window.location.href = `tel:${contact.phone}`}
                                                >
                                                    <Phone fontSize="small" />
                                                </IconButton>
                                            </Stack>
                                        )}
                                    </TableCell>
                                    <TableCell>{contact.billing_address || '-'}</TableCell>
                                    <TableCell>{contact.billing_city || '-'}</TableCell>
                                    <TableCell>{contact.billing_state || '-'}</TableCell>
                                    <TableCell>{contact.billing_postcode || '-'}</TableCell>
                                    <TableCell>
                                        <Stack direction="row" spacing={1}>
                                            {contact.has_woo && (
                                                <Chip 
                                                    label="WooCommerce" 
                                                    size="small" 
                                                    color="primary" 
                                                    variant="outlined" 
                                                />
                                            )}
                                            {contact.has_ghl && (
                                                <Chip 
                                                    label="GoHighLevel" 
                                                    size="small" 
                                                    color="secondary" 
                                                    variant="outlined" 
                                                />
                                            )}
                                            {!contact.has_woo && !contact.has_ghl && (
                                                <Chip 
                                                    label="CRM" 
                                                    size="small" 
                                                    color="default" 
                                                    variant="outlined" 
                                                />
                                            )}
                                        </Stack>
                                    </TableCell>
                                    <TableCell>
                                        <IconButton
                                            size="small"
                                            onClick={(event) => handleMenuOpen(event, contact)}
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
                    count={totalContacts}
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
                    <Edit sx={{ mr: 1 }} /> Edit
                </MenuItem>
                <MenuItem onClick={handleMenuClose}>
                    <Delete sx={{ mr: 1 }} /> Delete
                </MenuItem>
            </Menu>
        </Box>
    );
};

export default ContactsPage;
