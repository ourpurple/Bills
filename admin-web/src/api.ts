import { ref } from 'vue'
export const username = ref('')
let csrf = ''
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message)
  }
}
export async function api<T>(
  path: string,
  method = 'GET',
  body?: unknown,
  extraHeaders: Record<string, string> = {},
  rawBody?: BodyInit,
): Promise<T> {
  const response = await fetch('/api/v1' + path, {
    method,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(method !== 'GET' ? { 'X-CSRF-Token': csrf } : {}),
      ...extraHeaders,
    },
    ...(rawBody !== undefined
      ? { body: rawBody }
      : body === undefined
        ? {}
        : { body: JSON.stringify(body) }),
  })
  const data = await response.json()
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') {
      username.value = ''
      window.dispatchEvent(new Event('bills-session-expired'))
    }
    const message =
      data.error?.fields?.map((f: { message: string }) => f.message).join('；') ||
      data.error?.message ||
      '请求失败'
    throw new ApiError(data.error?.code || 'REQUEST_FAILED', message, response.status)
  }
  return data as T
}
export async function loadSession() {
  const data = await api<{ username: string; csrf_token: string }>('/auth/me')
  username.value = data.username
  csrf = data.csrf_token
}
export async function login(user: string, password: string) {
  const data = await api<{ username: string; csrf_token: string }>('/auth/login', 'POST', {
    username: user,
    password,
  })
  username.value = data.username
  csrf = data.csrf_token
}
export async function logout() {
  await api('/auth/logout', 'POST')
  username.value = ''
  csrf = ''
}

export async function allItems<T>(path: string): Promise<T[]> {
  const rows: T[] = []
  for (let page = 1; ; page++) {
    const data = await api<{ items: T[]; total: number }>(`${path}?page=${page}&page_size=100`)
    rows.push(...data.items)
    if (rows.length >= data.total || !data.items.length) return rows
  }
}

export async function uploadFile<T>(file: File): Promise<T> {
  return api<T>(
    '/admin/sources/upload',
    'POST',
    undefined,
    {
      'Content-Type': 'application/octet-stream',
      'X-Filename': encodeURIComponent(file.name),
    },
    file,
  )
}
