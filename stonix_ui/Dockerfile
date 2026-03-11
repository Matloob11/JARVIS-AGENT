# --- STAGE 1: Build ---
FROM node:20-slim as builder

WORKDIR /app

# Install dependencies
COPY stonix_ui/package*.json ./
RUN npm install

# Copy source and build
COPY stonix_ui/ .
RUN npm run build

# --- STAGE 2: Runtime (Static Server) ---
FROM node:20-slim
LABEL maintainer="JARVIS & ANNA Team"

WORKDIR /app

# Install a simple static server
RUN npm install -g serve

# Copy build artifacts from builder
COPY --from=builder /app/dist ./dist

# Port: 5173 (or whatever Vite uses, though 'serve' defaults to 3000)
EXPOSE 3000

CMD ["serve", "-s", "dist", "-l", "3000"]
