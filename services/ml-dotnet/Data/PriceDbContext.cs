using Microsoft.EntityFrameworkCore;
using PrecioLocal.Ml.Domain;

namespace PrecioLocal.Ml.Data;

/// <summary>
/// EF Core sobre la Postgres del scraper. Refleja el esquema de
/// <c>db/supabase/migrations/001</c> y <c>008_ml.sql</c>. Este servicio lee
/// histórico y métricas; no scrapea precios.
/// </summary>
public sealed class PriceDbContext(DbContextOptions<PriceDbContext> options) : DbContext(options)
{
    public DbSet<Product> Products => Set<Product>();

    public DbSet<Category> Categories => Set<Category>();

    public DbSet<Chain> Chains => Set<Chain>();

    public DbSet<PricePoint> PricePoints => Set<PricePoint>();

    public DbSet<MlModel> MlModels => Set<MlModel>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<Chain>(entity =>
        {
            entity.ToTable("chains");
            entity.HasKey(chain => chain.Id);
            entity.Property(chain => chain.Id).HasColumnName("id");
            entity.Property(chain => chain.Name).HasColumnName("name");
            entity.Property(chain => chain.Slug).HasColumnName("slug");
        });

        modelBuilder.Entity<Category>(entity =>
        {
            entity.ToTable("categories");
            entity.HasKey(category => category.Id);
            entity.Property(category => category.Id).HasColumnName("id");
            entity.Property(category => category.Name).HasColumnName("name");
            entity.Property(category => category.Slug).HasColumnName("slug");
        });

        modelBuilder.Entity<Product>(entity =>
        {
            entity.ToTable("products");
            entity.HasKey(product => product.Id);
            entity.Property(product => product.Id).HasColumnName("id");
            entity.Property(product => product.Name).HasColumnName("name");
            entity.Property(product => product.NormalizedName).HasColumnName("normalized_name");
            entity.Property(product => product.CategoryId).HasColumnName("category_id");
        });

        modelBuilder.Entity<PricePoint>(entity =>
        {
            entity.ToTable("price_points");
            entity.HasKey(point => point.Id);
            entity.Property(point => point.Id).HasColumnName("id");
            entity.Property(point => point.ProductId).HasColumnName("product_id");
            entity.Property(point => point.ChainId).HasColumnName("chain_id");
            entity.Property(point => point.Price).HasColumnName("price");
            entity.Property(point => point.CapturedAt).HasColumnName("captured_at");
            entity.HasIndex(point => new { point.ProductId, point.ChainId, point.CapturedAt });
        });

        modelBuilder.Entity<MlModel>(entity =>
        {
            entity.ToTable("ml_models");
            entity.HasKey(model => model.Id);
            entity.Property(model => model.Id).HasColumnName("id");
            entity.Property(model => model.Model).HasColumnName("model");
            entity.Property(model => model.Language).HasColumnName("language");
            entity.Property(model => model.ProductId).HasColumnName("product_id");
            entity.Property(model => model.ChainId).HasColumnName("chain_id");
            entity.Property(model => model.Mape).HasColumnName("mape");
            entity.Property(model => model.Rmse).HasColumnName("rmse");
            entity.Property(model => model.Mae).HasColumnName("mae");
            entity.Property(model => model.NumPoints).HasColumnName("n_points");
            entity.Property(model => model.TrainedAt).HasColumnName("trained_at");
            entity.Property(model => model.IsActive).HasColumnName("is_active");
            entity.HasIndex(model => new { model.ProductId, model.ChainId, model.IsActive });
        });
    }
}

public sealed class Chain
{
    public long Id { get; set; }

    public string Name { get; set; } = string.Empty;

    public string Slug { get; set; } = string.Empty;
}

public sealed class Product
{
    public long Id { get; set; }

    public string Name { get; set; } = string.Empty;

    public string NormalizedName { get; set; } = string.Empty;

    public long? CategoryId { get; set; }
}

public sealed class Category
{
    public long Id { get; set; }

    public string Name { get; set; } = string.Empty;

    public string Slug { get; set; } = string.Empty;
}

public sealed class PricePoint
{
    public long Id { get; set; }

    public long ProductId { get; set; }

    public long ChainId { get; set; }

    public decimal Price { get; set; }

    public DateTime CapturedAt { get; set; }
}

public sealed class MlModel
{
    public long Id { get; set; }

    public string Model { get; set; } = string.Empty;

    public string Language { get; set; } = string.Empty;

    public long ProductId { get; set; }

    public long ChainId { get; set; }

    public decimal? Mape { get; set; }

    public decimal? Rmse { get; set; }

    public decimal? Mae { get; set; }

    public int NumPoints { get; set; }

    public DateTime TrainedAt { get; set; }

    public bool IsActive { get; set; }
}
