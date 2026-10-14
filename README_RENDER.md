# Publicación privada en Render

Este proyecto usa un servidor web protegido por una clave compartida. El repositorio debe crearse como **privado**, ya que incluye la imagen de firma y la plantilla de la empresa.

## Publicar

1. Sube el contenido de este paquete a un repositorio **privado** de GitHub.
2. En Render, crea un servicio nuevo con **Blueprint** y conecta ese repositorio. Render detectará `render.yaml` y `Dockerfile`.
3. Cuando Render solicite `TREMOND_ACCESS_KEY`, crea una clave aleatoria larga y compártela solo con el personal que usará la aplicación.
4. Espera a que termine el despliegue. Render mostrará la URL pública `https://...onrender.com`.
5. Abre la URL, ingresa la clave y carga el SCTR vigente. El SCTR solo permanece en memoria del proceso de Render y debe cargarse otra vez si Render reinicia el servicio.

La lista SCTR y la firma solo se sirven a quien presenta la clave. Las solicitudes se generan en el servidor y se descargan en el navegador. No se escriben PDFs del SCTR en el disco de la instancia. Usa HTTPS de Render.

La cuenta de GitHub/Render y la clave del servicio son responsabilidad del titular. El código no incluye credenciales. Para usuarios de la página, solo se distribuye la clave compartida; nunca la imagen de firma por separado.
