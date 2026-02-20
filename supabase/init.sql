-- Media Assistant Database Schema

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Downloads table
CREATE TABLE downloads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(500) NOT NULL,
    type VARCHAR(50) NOT NULL CHECK (type IN ('movie', 'music', 'tv', 'audiobook')),
    magnet TEXT,
    torrent_hash VARCHAR(100),
    status VARCHAR(50) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'searching', 'downloading', 'completed', 'failed', 'cancelled')),
    progress INTEGER DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    download_speed BIGINT DEFAULT 0,
    upload_speed BIGINT DEFAULT 0,
    size BIGINT,
    downloaded BIGINT DEFAULT 0,
    save_path TEXT,
    final_path TEXT,
    quality VARCHAR(50),
    year INTEGER,
    imdb_id VARCHAR(50),
    tmdb_id INTEGER,
    source VARCHAR(100) DEFAULT 'manual',
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for downloads
CREATE INDEX idx_downloads_status ON downloads(status);
CREATE INDEX idx_downloads_type ON downloads(type);
CREATE INDEX idx_downloads_created_at ON downloads(created_at DESC);
CREATE INDEX idx_downloads_imdb_id ON downloads(imdb_id);

-- Library items table (synced from Jellyfin/Radarr)
CREATE TABLE library_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(500) NOT NULL,
    type VARCHAR(50) NOT NULL CHECK (type IN ('movie', 'show', 'episode', 'book', 'audiobook', 'music')),
    year INTEGER,
    overview TEXT,
    poster_url TEXT,
    backdrop_url TEXT,
    imdb_id VARCHAR(50),
    tmdb_id INTEGER,
    tvdb_id INTEGER,
    jellyfin_id VARCHAR(100),
    radarr_id INTEGER,
    lidarr_id INTEGER,
    file_path TEXT,
    file_size BIGINT,
    quality VARCHAR(50),
    genres TEXT[],
    actors TEXT[],
    director VARCHAR(200),
    studio VARCHAR(200),
    rating DECIMAL(3,1),
    watched BOOLEAN DEFAULT FALSE,
    watch_count INTEGER DEFAULT 0,
    last_watched_at TIMESTAMP WITH TIME ZONE,
    added_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for library
CREATE INDEX idx_library_type ON library_items(type);
CREATE INDEX idx_library_imdb ON library_items(imdb_id);
CREATE INDEX idx_library_tmdb ON library_items(tmdb_id);
CREATE INDEX idx_library_title ON library_items USING gin(to_tsvector('english', title));

-- Photos table (synced from Immich)
CREATE TABLE photos (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    immich_id UUID UNIQUE,
    device_asset_id VARCHAR(200),
    owner_id UUID,
    library_id UUID,
    type VARCHAR(50) DEFAULT 'IMAGE',
    original_path TEXT,
    original_file_name VARCHAR(500),
    thumbnail_path TEXT,
    description TEXT,
    exif_info JSONB,
    tags TEXT[],
    people TEXT[],
    is_favorite BOOLEAN DEFAULT FALSE,
    is_archived BOOLEAN DEFAULT FALSE,
    file_created_at TIMESTAMP WITH TIME ZONE,
    file_modified_at TIMESTAMP WITH TIME ZONE,
    taken_at TIMESTAMP WITH TIME ZONE,
    added_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for photos
CREATE INDEX idx_photos_taken_at ON photos(taken_at DESC);
CREATE INDEX idx_photos_tags ON photos USING gin(tags);
CREATE INDEX idx_photos_people ON photos USING gin(people);

-- Activity log table
CREATE TABLE activity_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type VARCHAR(100) NOT NULL,
    source VARCHAR(100) NOT NULL,
    message TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_activity_created_at ON activity_log(created_at DESC);
CREATE INDEX idx_activity_type ON activity_log(type);

-- Settings table
CREATE TABLE settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Insert default settings
INSERT INTO settings (key, value, description) VALUES
('download_path', '/downloads', 'Path for active downloads'),
('movies_path', '/mnt/cloud/gdrive/Media/Movies', 'Path for movies'),
('music_path', '/mnt/cloud/gdrive/Media/Music', 'Path for music'),
('tv_path', '/mnt/cloud/gdrive/Media/TV', 'Path for TV shows'),
('default_movie_quality', '1080p', 'Default quality for movie downloads'),
('default_music_format', 'FLAC', 'Default format for music downloads'),
('qbittorrent_url', 'http://localhost:8088', 'qBittorrent web UI URL'),
('radarr_url', 'http://localhost:7878', 'Radarr URL'),
('lidarr_url', 'http://localhost:8686', 'Lidarr URL');

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for updated_at
CREATE TRIGGER update_downloads_updated_at BEFORE UPDATE ON downloads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_library_updated_at BEFORE UPDATE ON library_items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
