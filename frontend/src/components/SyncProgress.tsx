import React from 'react';
import { Box, LinearProgress, Typography, Paper } from '@mui/material';

interface SyncProgressProps {
  progress?: {
    current: number;
    total: number;
    type: 'products' | 'customers' | 'orders';
  };
  status: 'success' | 'error' | 'in_progress' | 'stopped';
  message: string;
}

const SyncProgress: React.FC<SyncProgressProps> = ({ progress, status, message }) => {
  const getProgressValue = () => {
    if (!progress) return 0;
    return (progress.current / progress.total) * 100;
  };

  const getProgressColor = () => {
    switch (status) {
      case 'success':
        return 'success';
      case 'error':
        return 'error';
      case 'stopped':
        return 'warning';
      default:
        return 'primary';
    }
  };

  const getSyncTypeLabel = (type: string) => {
    switch (type) {
      case 'products':
        return 'Products';
      case 'customers':
        return 'Contacts';
      case 'orders':
        return 'Orders';
      default:
        return type;
    }
  };

  return (
    <Paper elevation={2} sx={{ p: 2, mb: 2 }}>
      <Box sx={{ width: '100%' }}>
        {progress && (
          <Box sx={{ mb: 1 }}>
            <Typography variant="subtitle1" color="primary" sx={{ fontWeight: 'medium' }}>
              Syncing {getSyncTypeLabel(progress.type)}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {progress.current} of {progress.total}
            </Typography>
          </Box>
        )}
        <Box sx={{ display: 'flex', alignItems: 'center' }}>
          <Box sx={{ width: '100%', mr: 1 }}>
            <LinearProgress
              variant={status === 'in_progress' ? 'determinate' : 'determinate'}
              value={status === 'in_progress' ? getProgressValue() : 100}
              color={getProgressColor()}
            />
          </Box>
        </Box>
        <Typography
          variant="body2"
          color={status === 'error' ? 'error' : 'text.secondary'}
          sx={{ mt: 1 }}
        >
          {message}
        </Typography>
      </Box>
    </Paper>
  );
};

export default SyncProgress;
