"""
Roles con permisos de administración del bot (!limpiar, !respaldo, !restaurar),
además de quien tenga el permiso de Administrador en el servidor.
Se comparan sin mayúsculas ni íconos (ej. "💻・Programadora" cuenta como "programadora").
"""
ADMIN_ROLES = {
    "desarrollador", "desarrolladores", "developer", "developers", "dev", "devs",
    "programador", "programadores", "programadora", "programadoras",
    "admin", "admins", "administrador", "administradores", "administradora", "administradoras", "administrator",
}

NO_PERMISSION_MESSAGE = "⛔ Solo los administradores, desarrolladores y programadores pueden usar este comando."
