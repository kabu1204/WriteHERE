import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import PropTypes from 'prop-types';
import { loginUser } from '../utils/api';

const AuthContext = createContext();

const getStoredItem = (key) => {
  return localStorage.getItem(key) || sessionStorage.getItem(key);
};

const persistAuth = (token, user, remember) => {
  const primaryStorage = remember ? localStorage : sessionStorage;
  const secondaryStorage = remember ? sessionStorage : localStorage;

  if (token && user) {
    primaryStorage.setItem('authToken', token);
    primaryStorage.setItem('authUser', JSON.stringify(user));
  }

  secondaryStorage.removeItem('authToken');
  secondaryStorage.removeItem('authUser');
};

export const AuthProvider = ({ children }) => {
  const [token, setToken] = useState(() => getStoredItem('authToken'));
  const [user, setUser] = useState(() => {
    const stored = getStoredItem('authUser');
    return stored ? JSON.parse(stored) : null;
  });
  const [authLoading, setAuthLoading] = useState(false);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    localStorage.removeItem('authToken');
    localStorage.removeItem('authUser');
    sessionStorage.removeItem('authToken');
    sessionStorage.removeItem('authUser');
  }, []);

  const login = useCallback(async (email, password, remember = true) => {
    setAuthLoading(true);
    try {
      const normalizedEmail = (email || '').trim().toLowerCase();
      const data = await loginUser(normalizedEmail, password);
      setToken(data.access_token);
      setUser(data.user);
      persistAuth(data.access_token, data.user, remember);
      return data;
    } finally {
      setAuthLoading(false);
    }
  }, []);

  useEffect(() => {
    const handleForcedLogout = () => {
      logout();
    };

    window.addEventListener('auth:logout', handleForcedLogout);
    return () => {
      window.removeEventListener('auth:logout', handleForcedLogout);
    };
  }, [logout]);

  const value = useMemo(() => ({
    user,
    token,
    login,
    logout,
    authLoading,
    isAuthenticated: Boolean(token)
  }), [user, token, login, logout, authLoading]);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

AuthProvider.propTypes = {
  children: PropTypes.node.isRequired,
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
