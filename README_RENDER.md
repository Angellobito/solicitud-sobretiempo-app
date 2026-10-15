# Solicitud de sobretiempo - Tremond

Aplicación web para generar el formato PDF y registrar la validación de Gerencia y Recursos Humanos.

## Configuración de Render

- `TREMOND_HOSTED=1`
- `TREMOND_ACCESS_KEY`: clave de acceso del personal.
- `TREMOND_APPROVAL_PASSWORD`: clave para el panel de validación. Se configura como secreto en el panel de Render; nunca se incluye en este repositorio.
- `TREMOND_DATABASE_URL`: cadena de conexión PostgreSQL, configurada como secreto en Render. Cuando existe, solicitudes y decisiones usan PostgreSQL en lugar de SQLite.
- `TREMOND_DATA_DIR=/var/data`: ubicación de SQLite para ejecución local o cuando no se haya definido PostgreSQL.
- `TREMOND_PERSISTENT_STORAGE=1` solo si se usa un disco persistente de Render para SQLite.

El estado final es **Aprobado** únicamente cuando Gerencia y Recursos Humanos aprueban. Si cualquiera deniega, el resultado final es **Denegado**. Se conserva un registro de cada decisión y cambio.

## Persistencia del historial

Render Free usa un sistema de archivos efímero: al dormir, reiniciarse o desplegarse el servicio se pierden los archivos locales. Para conservar el historial, crea una base PostgreSQL en Supabase y configura su URI de conexión como el secreto `TREMOND_DATABASE_URL` en Render. Usa el pooler de sesión si necesitas conexión IPv4 e incluye `sslmode=require`. Nunca guardes la cadena en el repositorio. La primera conexión crea las tablas necesarias. Si no se configura esa variable, la app usa SQLite y Render Free puede perder los datos al reiniciar.

El proyecto de Supabase debe permanecer activo para consultar el historial. El plan Free puede pausar proyectos inactivos; los datos se conservan, pero se debe reanudar el proyecto para volver a usarlo.

El repositorio público incluye los archivos de la plantilla y la imagen del sello/firma usados por el PDF.
