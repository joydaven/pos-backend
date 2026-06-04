# OAuth2 Integration Guide for Products API

## API Endpoints

- **Products API**: `/api/products/` - Requires OAuth2 authentication with 'products' scope
- **OAuth2 Endpoints**: `/o/` - Standard OAuth2 endpoints for authorization, token, etc.
- **API Documentation**: `/api/` - Lists available API endpoints

## OAuth2 Setup

1. **Create an OAuth2 Application**:
   - Log in to the Django admin panel at `/admin/`
   - Navigate to OAuth2_Provider > Applications
   - Create a new application with the following settings:
   - Client Type: Public
   - Authorization Grant Type: Authorization Code
   - Redirect URIs: Your React app's callback URL (e.g., `http://localhost:3000/callback`)
     - Scopes: `products`

2. **Make note of your Client ID** - You'll need this for your React application.

## Using the Products API from React

Here's an example of how to integrate with the Products API using OAuth2 in a React application:

```jsx
// oauth-config.js
export const OAUTH_CONFIG = {
  clientId: 'YOUR_CLIENT_ID',
  authorizeUrl: 'http://localhost:8000/o/authorize/',
  tokenUrl: 'http://localhost:8000/o/token/',
  redirectUri: 'http://localhost:3000/callback',
  scope: 'products',
};

// App.js
import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Route, Routes, Link } from 'react-router-dom';
import { OAUTH_CONFIG } from './oauth-config';

// Generate a random string for PKCE code verifier
function generateCodeVerifier() {
  const array = new Uint8Array(32);
  window.crypto.getRandomValues(array);
  return Array.from(array, (byte) => ('0' + byte.toString(16)).slice(-2)).join('');
}

// Generate code challenge from verifier
async function generateCodeChallenge(codeVerifier) {
  const encoder = new TextEncoder();
  const data = encoder.encode(codeVerifier);
  const digest = await window.crypto.subtle.digest('SHA-256', data);
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

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
          <Route path="/products" element={<Products />} />
          <Route path="/callback" element={<Callback />} />
        </Routes>
      </div>
    </Router>
  );
}

function Home() {
  return <h2>Welcome to the Doctors Studio API Demo</h2>;
}

function Products() {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [token, setToken] = useState(localStorage.getItem('access_token'));

  useEffect(() => {
    if (token) {
      fetchProducts();
    } else {
      initiateOAuth();
    }
  }, [token]);

  const fetchProducts = async () => {
    try {
      const response = await fetch('http://localhost:8000/api/products/', {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      
      if (!response.ok) {
        if (response.status === 401) {
          // Token expired or invalid
          localStorage.removeItem('access_token');
          setToken(null);
          return;
        }
        throw new Error(`API error: ${response.status}`);
      }
      
      const data = await response.json();
      setProducts(data.results || data);
      setLoading(false);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  const initiateOAuth = async () => {
    // Generate and store PKCE code verifier
    const codeVerifier = generateCodeVerifier();
    localStorage.setItem('code_verifier', codeVerifier);
    
    // Generate code challenge
    const codeChallenge = await generateCodeChallenge(codeVerifier);
    
    // Redirect to authorization endpoint
    const authUrl = new URL(OAUTH_CONFIG.authorizeUrl);
    authUrl.searchParams.append('client_id', OAUTH_CONFIG.clientId);
    authUrl.searchParams.append('response_type', 'code');
    authUrl.searchParams.append('redirect_uri', OAUTH_CONFIG.redirectUri);
    authUrl.searchParams.append('scope', OAUTH_CONFIG.scope);
    authUrl.searchParams.append('code_challenge', codeChallenge);
    authUrl.searchParams.append('code_challenge_method', 'S256');
    
    window.location.href = authUrl.toString();
  };

  if (!token) {
    return <p>Please log in to view products</p>;
  }

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

function Callback() {
  const [status, setStatus] = useState('Processing authentication...');

  useEffect(() => {
    const exchangeCodeForToken = async () => {
      try {
        // Get the authorization code from URL
        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get('code');
        
        if (!code) {
          setStatus('Error: No authorization code received');
          return;
        }
        
        // Get stored code verifier
        const codeVerifier = localStorage.getItem('code_verifier');
        if (!codeVerifier) {
          setStatus('Error: No code verifier found');
          return;
        }
        
        // Exchange code for token
        const tokenResponse = await fetch(OAUTH_CONFIG.tokenUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
          },
          body: new URLSearchParams({
            client_id: OAUTH_CONFIG.clientId,
            grant_type: 'authorization_code',
            code: code,
            redirect_uri: OAUTH_CONFIG.redirectUri,
            code_verifier: codeVerifier
          })
        });
        
        if (!tokenResponse.ok) {
          const errorData = await tokenResponse.json();
          throw new Error(errorData.error_description || 'Failed to exchange code for token');
        }
        
        const tokenData = await tokenResponse.json();
        
        // Store the access token
        localStorage.setItem('access_token', tokenData.access_token);
        
        // Clean up code verifier
        localStorage.removeItem('code_verifier');
        
        setStatus('Authentication successful! Redirecting...');
        
        // Redirect back to products page
        setTimeout(() => {
          window.location.href = '/products';
        }, 1000);
        
      } catch (err) {
        setStatus(`Error: ${err.message}`);
      }
    };
    
    exchangeCodeForToken();
  }, []);

  return <p>{status}</p>;
}

export default App;
```

## API Usage

The Products API supports the following operations:

- `GET /api/products/` - List all products (paginated)
- `GET /api/products/?search=keyword` - Search products by name, description, or categories
- `GET /api/products/?ordering=price` - Order products by price (use `-price` for descending)
- `GET /api/products/{id}/` - Get a specific product by ID

## Security Considerations

1. **Token Storage**: Store tokens securely in your React application. Prefer using memory or HTTP-only cookies over localStorage for production.

2. **HTTPS**: Always use HTTPS in production to protect token transmission.

3. **Token Refresh**: Implement token refresh logic for long-lived sessions.

4. **CORS**: The backend has CORS enabled, but you may need to configure it for specific domains in production.
