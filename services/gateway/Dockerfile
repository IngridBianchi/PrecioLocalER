# Build stage
FROM node:24-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy source
COPY src ./src
COPY tsconfig.json ./

# Build
RUN npm run build

# Runtime stage
FROM node:24-alpine AS runner

WORKDIR /app

# Create non-root user (use 1001 to avoid conflict with existing node user)
RUN addgroup -g 1001 -S appgroup && \
    adduser -u 1001 -S appuser -G appgroup

# Copy package files and install production deps only
COPY package*.json ./
RUN npm ci --omit=dev

# Copy built files
COPY --from=builder /app/dist ./dist
# Copy GraphQL schema files
COPY --from=builder /app/src/schema ./dist/schema

# Change ownership
RUN chown -R appuser:appgroup /app

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:4000/health || exit 1

EXPOSE 4000

ENV NODE_ENV=production

ENTRYPOINT ["node", "dist/index.js"]