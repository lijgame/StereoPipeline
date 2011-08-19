// __BEGIN_LICENSE__
// Copyright (C) 2006-2011 United States Government as represented by
// the Administrator of the National Aeronautics and Space Administration.
// All Rights Reserved.
// __END_LICENSE__


#include <vw/FileIO.h>
#include <vw/Image.h>
#include <vw/Cartography.h>
#include <vw/InterestPoint.h>
#include <vw/Math.h>
#include <vw/Mosaic/ImageComposite.h>
#include <asp/ControlNetTK/Equalization.h>
#include <asp/Core/Common.h>
#include <asp/Core/Macros.h>
#include <limits>

#include <boost/filesystem.hpp>
namespace fs = boost::filesystem;
namespace po = boost::program_options;

using std::cout;
using std::endl;
using std::string;

using namespace vw;
using namespace vw::cartography;
using namespace vw::ip;

// Allows FileIO to correctly read/write these pixel types
namespace vw {
  template<> struct PixelFormatID<Vector3>   { static const PixelFormatEnum value = VW_PIXEL_GENERIC_3_CHANNEL; };
}

template <int dim>
class HomogeneousTransformFunctor : public UnaryReturnSameType {
  Matrix<double,dim+1,dim+1> m_trans;

public:
  HomogeneousTransformFunctor(Matrix<double,dim+1,dim+1> trans) : m_trans(trans) {}

  inline Vector<double,dim> operator()(Vector<double,dim> pt) const {
    if (pt == Vector<double,dim>())
      return pt;

    Vector<double,dim+1> pt_h;
    subvector(pt_h, 0, dim) = pt;
    pt_h[dim] = 1;

    Vector<double,dim+1> result = m_trans * pt_h;
    if (result[dim] != 1)
      result /= result[dim];
    return subvector(result,0,dim);
  }
};

void match_orthoimages( string const& left_image_name,
                        string const& right_image_name,
                        std::vector<InterestPoint> & matched_ip1,
                        std::vector<InterestPoint> & matched_ip2,
                        size_t const& max_points )
{

  vw_out() << "\t--> Finding Interest Points for the orthoimages\n";

  fs::path left_image_path(left_image_name), right_image_path(right_image_name);
  string left_ip_file = change_extension(left_image_path, ".vwip").string();
  string right_ip_file = change_extension(right_image_path, ".vwip").string();
  string match_file = (left_image_path.branch_path() / (fs::basename(left_image_path) + "__" + fs::basename(right_image_path) + ".match")).string();

  // Building / Loading Interest point data
  if ( fs::exists(match_file) ) {

    vw_out() << "\t    * Using cached match file.\n";
    read_binary_match_file(match_file, matched_ip1, matched_ip2);
    vw_out() << "\t    * " << matched_ip1.size() << " matches\n";

  } else {
    std::vector<InterestPoint> ip1_copy, ip2_copy;

    if ( !fs::exists(left_ip_file) ||
         !fs::exists(right_ip_file) ) {

      // Worst case, no interest point operations have been performed before
      vw_out() << "\t    * Locating Interest Points\n";
      DiskImageView<PixelGray<float32> > left_disk_image(left_image_name);
      DiskImageView<PixelGray<float32> > right_disk_image(right_image_name);

      // Interest Point module detector code.
      OBALoGInterestOperator obalog_detector(0.03);
      IntegralInterestPointDetector<OBALoGInterestOperator> detector( obalog_detector, 200 );
      std::list<InterestPoint> ip1, ip2;
      vw_out() << "\t    * Processing " << left_image_name << "...\n" << std::flush;
      ip1 = detect_interest_points( left_disk_image, detector );
      vw_out() << "Located " << ip1.size() << " points.\n";
      vw_out() << "\t    * Processing " << right_image_name << "...\n" << std::flush;
      ip2 = detect_interest_points( right_disk_image, detector );
      vw_out() << "Located " << ip2.size() << " points.\n";

      vw_out() << "\t    * Generating descriptors..." << std::flush;
      SGradDescriptorGenerator descriptor;
      descriptor( left_disk_image, ip1 );
      descriptor( right_disk_image, ip2 );
      vw_out() << "done.\n";

      // Writing out the results
      vw_out() << "\t    * Caching interest points: "
               << left_ip_file << " & " << right_ip_file << std::endl;
      write_binary_ip_file( left_ip_file, ip1 );
      write_binary_ip_file( right_ip_file, ip2 );

    }

    vw_out() << "\t    * Using cached IPs.\n";
    ip1_copy = read_binary_ip_file(left_ip_file);
    ip2_copy = read_binary_ip_file(right_ip_file);

    vw_out() << "\t    * Matching interest points\n";
    ip::DefaultMatcher matcher(0.6);

    matcher(ip1_copy, ip2_copy, matched_ip1, matched_ip2,
            false, TerminalProgressCallback( "asp", "\t    Matching: "));
    ip::remove_duplicates(matched_ip1, matched_ip2);
    vw_out(InfoMessage) << "\t    " << matched_ip1.size() << " putative matches.\n";
    asp::cnettk::equalization( matched_ip1, matched_ip2, max_points );
    vw_out(InfoMessage) << "\t    " << matched_ip1.size() << " thinned matches.\n";

    vw_out() << "\t    * Caching matches: " << match_file << "\n";
    write_binary_match_file( match_file, matched_ip1, matched_ip2);
  }

}

struct Options : public asp::BaseOptions {
  Options() : dem1_nodata(std::numeric_limits<double>::quiet_NaN()), dem2_nodata(std::numeric_limits<double>::quiet_NaN()) {}
  // Input
  string dem1_name, dem2_name, ortho1_name, ortho2_name;
  double dem1_nodata, dem2_nodata;
  size_t max_points;

  // Output
  string output_prefix;
};

void handle_arguments( int argc, char *argv[], Options& opt ) {
  po::options_description general_options("");
  general_options.add_options()
    ("max-match-points", po::value(&opt.max_points)->default_value(800), "The max number of points that will be enforced after matching.")
    ("default-value", po::value(&opt.dem1_nodata), "The value of missing pixels in the first dem")
    ("output-prefix,o", po::value(&opt.output_prefix), "Specify the output prefix");
  general_options.add( asp::BaseOptionsDescription(opt) );

  po::options_description positional("");
  positional.add_options()
    ("dem1", po::value(&opt.dem1_name), "Explicitly specify the first dem")
    ("dem2", po::value(&opt.dem2_name), "Explicitly specify the second dem")
    ("ortho1", po::value(&opt.ortho1_name), "Explicitly specify the first orthoimage")
    ("ortho2", po::value(&opt.ortho2_name), "Explicitly specify the second orthoimage");

  po::positional_options_description positional_desc;
  positional_desc.add("dem1", 1);
  positional_desc.add("ortho1", 1);
  positional_desc.add("dem2", 1);
  positional_desc.add("ortho2", 1);

  std::string usage("<dem1> <ortho1> <dem2> <ortho2>");
  po::variables_map vm =
    asp::check_command_line( argc, argv, opt, general_options,
                             positional, positional_desc, usage );

  if ( opt.dem1_name.empty() || opt.dem2_name.empty() ||
       opt.ortho1_name.empty() || opt.ortho2_name.empty() )
    vw_throw( ArgumentErr() << "Missing input files.\n"
              << usage << general_options );
  if ( opt.output_prefix.empty() )
    opt.output_prefix = change_extension(fs::path(opt.dem1_name), "").string();
}

int main( int argc, char *argv[] ) {

  Options opt;
  try {
    handle_arguments( argc, argv, opt );

    DiskImageResourceGDAL ortho1_rsrc(opt.ortho1_name), ortho2_rsrc(opt.ortho2_name),
      dem1_rsrc(opt.dem1_name), dem2_rsrc(opt.dem2_name);

    // Pull out dem nodatas
    if ( opt.dem1_nodata == std::numeric_limits<double>::quiet_NaN() &&
         dem1_rsrc.has_nodata_read() ) {
      opt.dem1_nodata = dem1_rsrc.nodata_read();
      vw_out() << "\tFound DEM1 input nodata value: "
               << opt.dem1_nodata << endl;
    }
    if ( dem2_rsrc.has_nodata_read() ) {
      opt.dem2_nodata = dem2_rsrc.nodata_read();
      vw_out() << "\tFound DEM2 input nodata value: "
               << opt.dem2_nodata << endl;
    } else {
      vw_out() << "\tMissing nodata value for DEM2. Using DEM1 nodata value.\n";
      opt.dem2_nodata = opt.dem1_nodata;
    }

    typedef DiskImageView<double> dem_type;
    dem_type dem1_dmg(opt.dem1_name), dem2_dmg(opt.dem2_name);

    typedef InterpolationView<EdgeExtensionView<dem_type, ConstantEdgeExtension>, BilinearInterpolation> bilinear_type;
    typedef InterpolationView<EdgeExtensionView<dem_type, ConstantEdgeExtension>, NearestPixelInterpolation> nearest_type;
    bilinear_type dem1_interp =
      interpolate(dem1_dmg, BilinearInterpolation(), ConstantEdgeExtension());
    bilinear_type dem2_interp =
      interpolate(dem2_dmg, BilinearInterpolation(), ConstantEdgeExtension());
    nearest_type dem1_nearest =
      interpolate(dem1_dmg, NearestPixelInterpolation(), ConstantEdgeExtension());
    nearest_type dem2_nearest =
      interpolate(dem2_dmg, NearestPixelInterpolation(), ConstantEdgeExtension());

    GeoReference ortho1_georef, ortho2_georef, dem1_georef, dem2_georef;
    read_georeference(ortho1_georef, ortho1_rsrc);
    read_georeference(ortho2_georef, ortho2_rsrc);
    read_georeference(dem1_georef, dem1_rsrc);
    read_georeference(dem2_georef, dem2_rsrc);

    std::vector<InterestPoint> matched_ip1, matched_ip2;

    match_orthoimages(opt.ortho1_name, opt.ortho2_name,
                      matched_ip1, matched_ip2, opt.max_points);

    vw_out() << "\t--> Rejecting outliers using RANSAC.\n";
    std::vector<Vector4> ransac_ip1, ransac_ip2;
    for (size_t i = 0; i < matched_ip1.size(); i++) {
      Vector2 point1 = ortho1_georef.pixel_to_lonlat(Vector2(matched_ip1[i].x, matched_ip1[i].y));
      Vector2 point2 = ortho2_georef.pixel_to_lonlat(Vector2(matched_ip2[i].x, matched_ip2[i].y));

      Vector2 dem_pixel1 = dem1_georef.lonlat_to_pixel(point1);
      Vector2 dem_pixel2 = dem2_georef.lonlat_to_pixel(point2);

      if ( dem1_nearest(dem_pixel1.x(),dem_pixel1.y()) != opt.dem1_nodata &&
           dem2_nearest(dem_pixel2.x(),dem_pixel2.y()) != opt.dem2_nodata ) {

        double alt1 = dem1_georef.datum().radius(point1.x(), point1.y()) + dem1_interp(dem_pixel1.x(), dem_pixel1.y());
        double alt2 = dem2_georef.datum().radius(point2.x(), point2.y()) + dem2_interp(dem_pixel2.x(), dem_pixel2.y());

        Vector3 xyz1 = lon_lat_radius_to_xyz(Vector3(point1.x(), point1.y(), alt1));
        Vector3 xyz2 = lon_lat_radius_to_xyz(Vector3(point2.x(), point2.y(), alt2));

        ransac_ip1.push_back(Vector4(xyz1.x(), xyz1.y(), xyz1.z(), 1));
        ransac_ip2.push_back(Vector4(xyz2.x(), xyz2.y(), xyz2.z(), 1));
      } else {
        vw_out() << "Actually dropped something.\n";
      }
    }

    std::vector<int> indices;
    Matrix<double> trans;
    math::RandomSampleConsensus<math::AffineFittingFunctorN<3>,math::L2NormErrorMetric>
      ransac( math::AffineFittingFunctorN<3>(), math::L2NormErrorMetric(), 10);
    trans = ransac(ransac_ip1, ransac_ip2);
    indices = ransac.inlier_indices(trans, ransac_ip1, ransac_ip2);

    vw_out() << "\t    * Ransac Result: " << trans << "\n";
    vw_out() << "\t                     # inliers: " << indices.size() << "\n";

    { // Saving transform to human readable text
      std::string filename = (fs::path(opt.dem1_name).branch_path() / (fs::basename(fs::path(opt.dem1_name)) + "__" + fs::basename(fs::path(opt.dem2_name)) + "-Matrix.txt")).string();
      std::ofstream ofile( filename.c_str() );
      ofile << std::setprecision(15) << std::flush;
      ofile << "# inliers: " << indices.size() << endl;
      ofile << trans << endl;
      ofile.close();
    }

    ImageViewRef<PixelMask<double> > dem1_masked(create_mask(dem1_dmg, opt.dem1_nodata));

    ImageViewRef<Vector3> point_cloud =
      lon_lat_radius_to_xyz(project_point_image(dem_to_point_image(dem1_masked, dem1_georef), dem1_georef, false));
    ImageViewRef<Vector3> point_cloud_trans =
      per_pixel_filter(point_cloud, HomogeneousTransformFunctor<3>(trans));

    DiskImageResourceGDAL point_cloud_rsrc(opt.output_prefix + "-PC.tif",
                                           point_cloud_trans.format(),
                                           opt.raster_tile_size,
                                           opt.gdal_options);
    block_write_image(point_cloud_rsrc, point_cloud_trans,
                      TerminalProgressCallback("asp", "\t--> Transforming: "));
  } ASP_STANDARD_CATCHES;

  return 0;
}
