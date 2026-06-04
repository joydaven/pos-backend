# Token Refresh Implementation for React Frontend

## Overview

When using OAuth2 with your React application, you'll need to handle token refresh when the access token expires. Here's a complete implementation for token refresh.

## 1. Store Both Access and Refresh Tokens

When you first authenticate, store both the access token and refresh token:

```javascript
// After successful authentication
const handleTokenResponse = (tokenData) => {
  localStorage.setItem('access_token', tokenData.access_token);
  localStorage.setItem('refresh_token', tokenData.refresh_token);
  localStorage.setItem('token_expiry', Date.now() + (tokenData.expires_in * 1000));
};
```

## 2. Create an Axios Instance with Interceptors

```javascript
// api.js
import axios from 'axios';

const API_URL = 'http://127.0.0.1:8000';
const CLIENT_ID = 'YOUR_CLIENT_ID';

// Create axios instance
const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add request interceptor to add the access token to all requests
api.interceptors.request.use(
  async (config) => {
    // Don't add token for token refresh requests
    if (config.url === '/o/token/') {
      return config;
    }
    
    const token = await getValidToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Function to check if token is expired
const isTokenExpired = () => {
  const expiry = localStorage.getItem('token_expiry');
  return !expiry || Date.now() > parseInt(expiry);
};

// Function to refresh the token
const refreshToken = async () => {
  const refreshToken = localStorage.getItem('refresh_token');
  
  if (!refreshToken) {
    // No refresh token, user needs to log in again
    return null;
  }
  
  try {
    const response = await axios.post(`${API_URL}/o/token/`, 
      new URLSearchParams({
        'grant_type': 'refresh_token',
        'refresh_token': refreshToken,
        'client_id': CLIENT_ID,
      }), {
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded'
        }
      }
    );
    
    // Store new tokens
    localStorage.setItem('access_token', response.data.access_token);
    localStorage.setItem('refresh_token', response.data.refresh_token || refreshToken);
    localStorage.setItem('token_expiry', Date.now() + (response.data.expires_in * 1000));
    
    return response.data.access_token;
  } catch (error) {
    // Refresh token is invalid or expired
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('token_expiry');
    return null;
  }
};

// Get a valid token (refresh if needed)
const getValidToken = async () => {
  const token = localStorage.getItem('access_token');
  
  if (!token) {
    return null;
  }
  
  if (isTokenExpired()) {
    return await refreshToken();
  }
  
  return token;
};

export default api;
```

## 3. Use the API Instance in Your Components

```javascript
// ProductsList.js
import React, { useState, useEffect } from 'react';
import api from './api';

function ProductsList() {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchProducts = async () => {
      try {
        setLoading(true);
        const response = await api.get('/api/products/');
        setProducts(response.data.results || response.data);
        setLoading(false);
      } catch (err) {
        // If error is 401 (Unauthorized) and we couldn't refresh the token
        if (err.response && err.response.status === 401) {
          // Redirect to login
          window.location.href = '/login';
        } else {
          setError(err.message || 'Error fetching products');
          setLoading(false);
        }
      }
    };

    fetchProducts();
  }, []);

  if (loading) return <p>Loading products...</p>;
  if (error) return <p>Error: {error}</p>;

  return (
    <div>
      <h2>Products</h2>
      <ul>
        {products.map(product => (
          <li key={product.id}>
            <h3>{product.name}</h3>
            <p>{product.description}</p>
            <p>Price: ${product.price}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default ProductsList;
```

## 4. Complete Authentication Flow with Refresh

Here's a complete authentication service that handles the entire OAuth flow including token refresh:

```javascript
// authService.js
import axios from 'axios';

const API_URL = 'http://127.0.0.1:8000';
const CLIENT_ID = 'YOUR_CLIENT_ID';
const REDIRECT_URI = 'http://localhost:3000/auth/callback';

// Generate a random string for PKCE code verifier
export const generateCodeVerifier = () => {
  const array = new Uint8Array(32);
  window.crypto.getRandomValues(array);
  return Array.from(array, (byte) => ('0' + byte.toString(16)).slice(-2)).join('');
};

// Generate code challenge from verifier
export const generateCodeChallenge = async (codeVerifier) => {
  const encoder = new TextEncoder();
  const data = encoder.encode(codeVerifier);
  const digest = await window.crypto.subtle.digest('SHA-256', data);
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
};

// Initiate OAuth flow
export const initiateOAuth = async () => {
  // Generate and store PKCE code verifier
  const codeVerifier = generateCodeVerifier();
  localStorage.setItem('code_verifier', codeVerifier);
  
  // Generate code challenge
  const codeChallenge = await generateCodeChallenge(codeVerifier);
  
  // Redirect to authorization endpoint
  const authUrl = new URL(`${API_URL}/o/authorize/`);
  authUrl.searchParams.append('client_id', CLIENT_ID);
  authUrl.searchParams.append('response_type', 'code');
  authUrl.searchParams.append('redirect_uri', REDIRECT_URI);
  authUrl.searchParams.append('scope', 'products');
  authUrl.searchParams.append('code_challenge', codeChallenge);
  authUrl.searchParams.append('code_challenge_method', 'S256');
  
  window.location.href = authUrl.toString();
};

// Exchange authorization code for tokens
export const exchangeCodeForToken = async (code) => {
  try {
    const codeVerifier = localStorage.getItem('code_verifier');
    
    if (!codeVerifier) {
      throw new Error('No code verifier found');
    }
    
    const response = await axios.post(`${API_URL}/o/token/`, 
      new URLSearchParams({
        'client_id': CLIENT_ID,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
        'code_verifier': codeVerifier
      }), {
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded'
        }
      }
    );
    
    // Store tokens
    localStorage.setItem('access_token', response.data.access_token);
    localStorage.setItem('refresh_token', response.data.refresh_token);
    localStorage.setItem('token_expiry', Date.now() + (response.data.expires_in * 1000));
    
    // Clean up code verifier
    localStorage.removeItem('code_verifier');
    
    return response.data;
  } catch (error) {
    console.error('Error exchanging code for token:', error);
    throw error;
  }
};

// Refresh access token
export const refreshAccessToken = async () => {
  const refreshToken = localStorage.getItem('refresh_token');
  
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }
  
  try {
    const response = await axios.post(`${API_URL}/o/token/`, 
      new URLSearchParams({
        'grant_type': 'refresh_token',
        'refresh_token': refreshToken,
        'client_id': CLIENT_ID,
      }), {
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded'
        }
      }
    );
    
    // Store new tokens
    localStorage.setItem('access_token', response.data.access_token);
    
    // Some OAuth servers might issue a new refresh token
    if (response.data.refresh_token) {
      localStorage.setItem('refresh_token', response.data.refresh_token);
    }
    
    localStorage.setItem('token_expiry', Date.now() + (response.data.expires_in * 1000));
    
    return response.data;
  } catch (error) {
    // If refresh fails, clear tokens and require re-authentication
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('token_expiry');
    throw error;
  }
};

// Check if user is authenticated
export const isAuthenticated = () => {
  const token = localStorage.getItem('access_token');
  const expiry = localStorage.getItem('token_expiry');
  
  if (!token || !expiry) {
    return false;
  }
  
  // If token is expired but we have a refresh token, we consider the user still authenticated
  // The actual refresh will happen when making API calls
  return true;
};

// Logout
export const logout = () => {
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('token_expiry');
  window.location.href = '/login';
};

export default {
  initiateOAuth,
  exchangeCodeForToken,
  refreshAccessToken,
  isAuthenticated,
  logout
};
```

## 5. Callback Component to Handle Authorization Code

```javascript
// Callback.js
import React, { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { exchangeCodeForToken } from './authService';

function Callback() {
  const [status, setStatus] = useState('Processing authentication...');
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const handleCallback = async () => {
      try {
        // Get the authorization code from URL
        const searchParams = new URLSearchParams(location.search);
        const code = searchParams.get('code');
        
        if (!code) {
          setStatus('Error: No authorization code received');
          return;
        }
        
        // Exchange code for token
        await exchangeCodeForToken(code);
        
        setStatus('Authentication successful! Redirecting...');
        
        // Redirect to products page
        setTimeout(() => {
          navigate('/products');
        }, 1000);
        
      } catch (err) {
        setStatus(`Error: ${err.message}`);
      }
    };
    
    handleCallback();
  }, [location, navigate]);

  return <p>{status}</p>;
}

export default Callback;
```

## 6. Protected Route Component

```javascript
// ProtectedRoute.js
import React, { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { isAuthenticated } from './authService';

function ProtectedRoute({ children }) {
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    const checkAuth = async () => {
      const auth = isAuthenticated();
      setAuthenticated(auth);
      setLoading(false);
    };
    
    checkAuth();
  }, []);

  if (loading) {
    return <div>Loading...</div>;
  }

  return authenticated ? children : <Navigate to="/login" />;
}

export default ProtectedRoute;
```

## 7. App Component with Routes

```javascript
// App.js
import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import ProductsList from './ProductsList';
import Callback from './Callback';
import Login from './Login';
import ProtectedRoute from './ProtectedRoute';

function App() {
  return (
    <Router>
      <div className="App">
        <nav>
          <ul>
            <li><Link to="/">Home</Link></li>
            <li><Link to="/products">Products</Link></li>
          </ul>
        </nav>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
          <Route path="/auth/callback" element={<Callback />} />
          <Route 
            path="/products" 
            element={
              <ProtectedRoute>
                <ProductsList />
              </ProtectedRoute>
            } 
          />
        </Routes>
      </div>
    </Router>
  );
}

function Home() {
  return <h2>Welcome to the Doctors Studio API Demo</h2>;
}

export default App;
```

## 8. Login Component

```javascript
// Login.js
import React from 'react';
import { initiateOAuth } from './authService';

function Login() {
  const handleLogin = async () => {
    await initiateOAuth();
  };

  return (
    <div>
      <h2>Login</h2>
      <button onClick={handleLogin}>
        Login with OAuth
      </button>
    </div>
  );
}

export default Login;
```

## Testing the Token Refresh

To test the token refresh functionality:

1. Set a short expiration time for access tokens in your Django settings (e.g., 1 minute)
2. Log in to your application
3. Wait for the token to expire
4. Make an API request - the interceptor should automatically refresh the token
5. Verify the request succeeds with the new token

## Important Notes

1. **Security**: In a production environment, consider using HTTP-only cookies instead of localStorage for token storage to mitigate XSS attacks.

2. **Error Handling**: The implementation includes basic error handling, but you might want to add more sophisticated handling for specific error cases.

3. **Token Expiry**: This implementation uses a client-side expiry check. For more accuracy, you could decode the JWT token (if you're using JWT) to get the exact expiration time.

4. **Silent Refresh**: For a better user experience, you could implement a silent refresh that happens in the background before the token expires.

5. **Concurrent Requests**: If multiple requests are made while a token refresh is in progress, you might want to implement a queue system to avoid multiple refresh requests.
