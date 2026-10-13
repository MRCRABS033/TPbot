import asyncio

from discord.ext import tasks

from config import logger

# Espera antes de reintentar una tarea que falló (evita reintentos sin pausa si el error se repite)
RESTART_DELAY = 30


def resilient(loop: tasks.Loop):
    """
    Hace que una tarea automática (@tasks.loop) se recupere sola si falla.
    Por defecto discord.py detiene la tarea para siempre ante un error inesperado; con esto
    se registra el error en el log y la tarea vuelve a arrancar a los RESTART_DELAY segundos.

    Uso (encima de @tasks.loop):
        @resilient
        @tasks.loop(seconds=60)
        async def mi_tarea(self): ...
    """
    name = loop.coro.__name__

    @loop.error
    async def _on_error(*args):
        error = args[-1]
        logger.error(f"❌ La tarea '{name}' falló y se reiniciará en {RESTART_DELAY}s", exc_info=error)
        await asyncio.sleep(RESTART_DELAY)
        # En un cog, la tarea que corre es la copia ligada a la instancia (args[0])
        running = getattr(args[0], name, loop) if len(args) > 1 else loop
        # El error ya quedó en el log: se marca como leído para que asyncio no lo vuelva a mostrar
        failed_task = running.get_task()
        if failed_task:
            failed_task.add_done_callback(lambda t: t.cancelled() or t.exception())
        running.restart()

    return loop
