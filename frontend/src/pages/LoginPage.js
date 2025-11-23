import React, { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Container,
  Divider,
  FormControlLabel,
  Link,
  Paper,
  Stack,
  TextField,
  Typography,
  Alert
} from '@mui/material';
import LockOutlinedIcon from '@mui/icons-material/LockOutlined';
import { useNavigate, useLocation, Link as RouterLink } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

const LoginPage = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, authLoading, isAuthenticated } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [rememberMe, setRememberMe] = useState(true);
  const [error, setError] = useState('');

  const redirectPath = useMemo(() => {
    const requested = location.state?.from;
    return typeof requested === 'string' && requested.startsWith('/') ? requested : '/';
  }, [location.state]);

  useEffect(() => {
    if (isAuthenticated) {
      navigate(redirectPath, { replace: true });
    }
  }, [isAuthenticated, navigate, redirectPath]);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');

    if (!email || !password) {
      setError('Please enter both email and password to continue.');
      return;
    }

    try {
      await login(email.trim().toLowerCase(), password, rememberMe);
      navigate(redirectPath, { replace: true });
    } catch (err) {
      const message = err?.response?.data?.error || 'Invalid email or password.';
      setError(message);
    }
  };

  return (
    <Container maxWidth="sm" sx={{ py: { xs: 6, md: 10 } }}>
      <Paper
        elevation={3}
        sx={{
          p: { xs: 4, md: 6 },
          borderRadius: 4,
          background: 'linear-gradient(145deg, rgba(84, 54, 218, 0.02), rgba(16, 163, 127, 0.04))'
        }}
      >
        <Stack spacing={2} alignItems="center" sx={{ mb: 2 }}>
          <Box
            sx={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 64,
              height: 64,
              borderRadius: '50%',
              backgroundColor: 'primary.main',
              color: 'primary.contrastText',
              boxShadow: '0px 10px 20px rgba(84, 54, 218, 0.25)'
            }}
          >
            <LockOutlinedIcon fontSize="large" />
          </Box>
          <Box textAlign="center">
            <Typography variant="h4" gutterBottom>
              Welcome Back
            </Typography>
            <Typography variant="body1" color="text.secondary">
              Sign in to access your saved tasks and continue writing with WriteHERE.
            </Typography>
          </Box>
        </Stack>

        <Box component="form" onSubmit={handleSubmit} noValidate>
          <Stack spacing={3}>
            <TextField
              label="Email address"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              fullWidth
              required
              autoComplete="email"
            />

            <TextField
              label="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              fullWidth
              required
              autoComplete="current-password"
            />

            <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ xs: 'flex-start', sm: 'center' }}>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={rememberMe}
                    onChange={(event) => setRememberMe(event.target.checked)}
                    color="primary"
                  />
                }
                label="Keep me signed in"
              />
              <Link component={RouterLink} to="#" underline="hover" color="primary.main">
                Forgot password?
              </Link>
            </Stack>

            {error && <Alert severity="error">{error}</Alert>}
            <Alert severity="info">
              Credentials are sourced from your backend admin users file (default <code>backend/admin_users.json</code>). Ask your workspace admin for the correct email and password.
            </Alert>

            <Button
              type="submit"
              variant="contained"
              color="primary"
              size="large"
              fullWidth
              disabled={authLoading}
            >
              {authLoading ? 'Signing In…' : 'Sign In'}
            </Button>
          </Stack>
        </Box>

        <Divider sx={{ my: 4 }}>
          <Typography variant="caption" color="text.secondary">
            Access is limited to the local admin account
          </Typography>
        </Divider>

        <Stack spacing={1} alignItems="center">
          <Typography variant="body2" color="text.secondary">
            Registration is disabled in this preview build. Contact your administrator to rotate credentials.
          </Typography>
        </Stack>
      </Paper>
    </Container>
  );
};

export default LoginPage;
