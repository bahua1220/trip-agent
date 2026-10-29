import axios from 'axios'
const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const KEY = 'tripAuth'
export interface AuthSession { access_token: string; user: { id: string; username: string } }
export const getSession = (): AuthSession | null => { try { return JSON.parse(localStorage.getItem(KEY) || '') } catch { return null } }
const notifyAuthChanged = () => window.dispatchEvent(new Event('trip-auth-changed'))
export const setSession = (session: AuthSession) => { localStorage.setItem(KEY, JSON.stringify(session)); notifyAuthChanged() }
export const clearSession = () => { localStorage.removeItem(KEY); notifyAuthChanged() }
export const authRequest = async (path: string, username: string, password: string) => { const { data } = await axios.post<AuthSession>(`${baseURL}/api/auth${path}`, { username, password }); setSession(data); return data }
