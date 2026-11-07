FROM nginx:1.27-alpine

# Remove default nginx configurations
RUN rm -rf /etc/nginx/conf.d/* /etc/nginx/nginx.conf

# Copy hardened nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Generate fallback self-signed TLS certificates for bootstrap / healthchecks
RUN apk add --no-cache openssl && \
    mkdir -p /etc/nginx/certs && \
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
      -keyout /etc/nginx/certs/privkey.pem \
      -out /etc/nginx/certs/fullchain.pem \
      -subj "/C=US/ST=State/L=City/O=MAYA/CN=localhost" && \
    chmod 600 /etc/nginx/certs/privkey.pem && \
    chmod 644 /etc/nginx/certs/fullchain.pem && \
    apk del openssl

# Set permissions for non-root runtime compatibility
RUN touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/run/nginx.pid /var/cache/nginx /etc/nginx/certs

EXPOSE 80 443

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD wget --spider -q http://localhost:80/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
