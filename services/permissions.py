from channels import _normalize
from constants.roles import ADMIN_ROLES


def is_bot_admin(ctx):
    """True si quien usa el comando es administrador del servidor o tiene un rol de ADMIN_ROLES."""
    if not ctx.guild:
        return False
    if ctx.author.guild_permissions.administrator:
        return True
    return any(_normalize(role.name) in ADMIN_ROLES for role in ctx.author.roles)
