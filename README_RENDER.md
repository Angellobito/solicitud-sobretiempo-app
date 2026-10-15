# Solicitud de sobretiempo - Tremond

Aplicación web para generar el formato PDF y registrar la validación de Gerencia y Recursos Humanos.

## Configuración de Render

- `TREMOND_HOSTED=1`
- `TREMOND_ACCESS_KEY`: clave de acceso del personal.
- `TREMOND_APPROVAL_PASSWORD`: clave para el panel de validación. Se configura como secreto en el panel de Render; nunca se incluye en este repositorio.
- `TREMOND_DATA_DIR=/var/data`: ubicación de la base SQLite.
- `TREMOND_PERSISTENT_STORAGE=1` solo después de adjuntar un disco persistente montado en `/var/data`.

El estado final es **Aprobado** únicamente cuando Gerencia y Recursos Humanos aprueban. Si cualquiera deniega, el resultado final es **Denegado**. Se conserva un registro de cada decisión y cambio.

## Persistencia del historial

Render Free usa un sistema de archivos efímero: al dormir, reiniciarse o desplegarse el servicio se pierden los archivos locales. Por eso el panel avisa que el historial no es permanente mientras no se configure almacenamiento persistente. Un disco de Render requiere un servicio de pago; no habilitarlo sin aprobación del titular.

El repositorio público incluye los archivos de la plantilla y la imagen del sello/firma usados por el PDF.
