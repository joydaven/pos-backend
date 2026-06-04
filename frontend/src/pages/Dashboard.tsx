import React, { useState, useEffect, useCallback } from 'react';
import { Box, Button, Container, Typography, Paper } from '@mui/material';
import SyncIcon from '@mui/icons-material/Sync';
import { syncApi } from '../services/api';
import SyncProgress from '../components/SyncProgress';

interface SyncStatus {
  status: 'success' | 'error' | 'in_progress' | 'idle' | 'stopped';
  message: string;
  progress?: {
    current: number;
    total: number;
    type: 'products' | 'customers' | 'orders';
  };
}

const Dashboard: React.FC = () => {
  const [syncStatus, setSyncStatus] = useState<SyncStatus>({
    status: 'idle',
    message: ''
  });

  const pollProgress = useCallback(async () => {
    if (syncStatus.status === 'in_progress') {
      const status = await syncApi.getSyncProgress();
      setSyncStatus(status);
      if (status.status === 'in_progress') {
        setTimeout(pollProgress, 1000);
      }
    }
  }, [syncStatus.status]);

  useEffect(() => {
    if (syncStatus.status === 'in_progress') {
      pollProgress();
    }
  }, [syncStatus.status, pollProgress]);

  const handleSync = async () => {
    setSyncStatus({
      status: 'in_progress',
      message: 'Starting sync...',
      progress: {
        current: 0,
        total: 100,
        type: 'products'
      }
    });

    try {
      const response = await syncApi.syncWooCommerceData();
      setSyncStatus(response);
      if (response.status === 'in_progress') {
        pollProgress();
      }
    } catch (error) {
      setSyncStatus({
        status: 'error',
        message: error instanceof Error ? error.message : 'An error occurred during sync'
      });
    }
  };

  return (
    <Container maxWidth="lg" sx={{ mt: 4, mb: 4 }}>
      <Paper sx={{ p: 3, mb: 3 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
          <Typography variant="h5" component="h2">
            WooCommerce Sync
          </Typography>
          <Button
            variant="contained"
            color="primary"
            startIcon={<SyncIcon />}
            onClick={handleSync}
            disabled={syncStatus.status === 'in_progress'}
          >
            Sync Now
          </Button>
        </Box>

        {syncStatus.status !== 'idle' && (
          <SyncProgress
            status={syncStatus.status}
            message={syncStatus.message}
            progress={syncStatus.progress}
          />
        )}
      </Paper>
    </Container>
  );
};

export default Dashboard;
