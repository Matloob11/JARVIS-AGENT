import axios from 'axios';

axios.defaults.baseURL = import.meta.env.VITE_VORTEX_URL || 'http://127.0.0.1:5001';
axios.defaults.headers['Content-Type'] = 'application/json';

export const setAxiosAuth = (token?: string) =>
  (axios.defaults.headers.common['Authorization'] = token
    ? `Bearer ${token}`
    : undefined);
